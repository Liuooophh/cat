"""Time based entry / loop / exit playback, independent of the desktop UI."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections.abc import Mapping


@dataclass(frozen=True)
class ClipSpec:
    """Frame ranges are zero based and exclude their right hand endpoint."""

    fps: float
    frame_count: int
    play_range: tuple[int, int]
    loop: bool = True
    source_facing: str = "left"
    stages: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    movement_range: tuple[int, int] | None = None
    loop_blend_frames: int = 0

    @property
    def duration(self) -> float:
        return (self.play_range[1] - self.play_range[0]) / self.fps

    def stage_duration(self, phase: str) -> float:
        bounds = self.stages.get(phase)
        return (bounds[1] - bounds[0]) / self.fps if bounds else 0.0


@dataclass(frozen=True)
class MotionPose:
    state: str
    phase: str
    frame_index: int
    movement_weight: float = 0.0
    blend_from_index: int | None = None
    blend_amount: float = 0.0


class MotionPlayer:
    """Run complete gestures without restarting on repeated behavior requests.

    Only stable idle accepts a new action. An idle request lets a staged action
    finish its current loop, then plays its exit once. Grooming finishes its
    complete one-shot clip. ``force_idle`` is reserved for an explicit hard reset.
    Every clock value uses the caller's same monotonic seconds.
    """

    def __init__(self, specs: Mapping[str, ClipSpec], now: float = 0.0,
                 *, idle_entries: Mapping[str, int] | None = None) -> None:
        if "idle" not in specs:
            raise ValueError("MotionPlayer requires an idle clip")
        self.specs = dict(specs)
        self.idle_entries = dict(idle_entries or {})
        idle_first, idle_end = self.specs["idle"].play_range
        for state, index in self.idle_entries.items():
            if (state not in self.specs or not isinstance(index, int)
                    or isinstance(index, bool) or not idle_first <= index < idle_end):
                raise ValueError("idle entry must identify an existing idle frame")
        self.state = "idle"
        self.phase = "loop"
        self._phase_started = float(now)
        self._last_now = float(now)
        self._stop_requested = False
        self._terminal_at: float | None = None
        self._request_key: tuple[str, float | None] = ("idle", None)
        self._loop_repeats = 0

    @property
    def is_idle(self) -> bool:
        return self.state == "idle"

    def _clock(self, now: float) -> float:
        if not math.isfinite(now):
            raise ValueError("motion time must be finite")
        self._last_now = max(self._last_now, float(now))
        return self._last_now

    def _bounds(self) -> tuple[int, int]:
        spec = self.specs[self.state]
        return spec.stages.get(self.phase, spec.play_range)

    def _begin(self, state: str, now: float, started_at: float | None) -> None:
        previous_state = self.state
        self.state = state
        spec = self.specs[state]
        self.phase = "enter" if spec.stages else (
            "once" if state == "groom" or not spec.loop else "loop")
        self._phase_started = min(now, started_at) if started_at is not None else now
        if state == "idle" and previous_state != "idle":
            first, _ = spec.play_range
            entry = self.idle_entries.get(previous_state, first)
            self._phase_started -= (entry - first) / spec.fps
        self._stop_requested = False
        self._terminal_at = None
        self._loop_repeats = 0

    def shift_time(self, seconds: float) -> None:
        """Resume the same frame and request identity after a hidden interval."""
        self._phase_started += seconds
        self._last_now += seconds
        if self._terminal_at is not None:
            self._terminal_at += seconds
        state, started = self._request_key
        if started is not None:
            self._request_key = (state, started+seconds)

    def force_idle(self, now: float) -> MotionPose:
        """Immediately cancel any gesture for an explicit hard reset."""
        now = self._clock(now)
        self._begin("idle", now, None)
        return self._pose(now)

    def update(self, desired_state: str, now: float,
               started_at: float | None = None) -> MotionPose:
        """Submit behavior intent and return the current pose.

        ``started_at`` identifies a behavior request; repeating it is idempotent.
        Omitting it uses changes of desired state as the request identity.
        Requests arriving during an action are not queued to interrupt its exit.
        """
        now = self._clock(now)
        if started_at is not None and not math.isfinite(started_at):
            raise ValueError("motion start time must be finite")
        desired = desired_state if desired_state in self.specs else "idle"
        request = (desired, started_at)
        changed = request != self._request_key
        was_idle = self.is_idle
        if desired == "idle" and not self.is_idle:
            self._stop_requested = True
        self._advance(now)
        if was_idle and self.is_idle and desired != "idle" and changed:
            self._begin(desired, now, started_at)
            self._advance(now)
        self._request_key = request
        return self._pose(now)

    def sample(self, now: float) -> MotionPose:
        """Advance time without issuing a new behavior request."""
        now = self._clock(now)
        self._advance(now)
        return self._pose(now)

    def _advance(self, now: float) -> None:
        if self._terminal_at is not None:
            # Deliver the actual final frame once, even after a delayed UI tick.
            # A second read at the same timestamp must not consume this hold.
            if now > self._terminal_at:
                self._begin("idle", now, None)
            return
        spec = self.specs[self.state]
        while True:
            first, end = self._bounds()
            duration = (end - first) / spec.fps
            elapsed = max(0.0, now - self._phase_started)
            if elapsed + 1e-9 < duration:
                return
            if self.phase == "enter":
                self.phase = "loop"
                self._phase_started += duration
                # Always play at least one complete stable loop after entering.
                continue
            if self.phase == "loop":
                if self.state != "idle" and self._stop_requested:
                    if spec.stages:
                        self.phase = "exit"
                        self._phase_started += duration
                        continue
                    self._terminal_at = now
                    return
                cycles = max(1, int((elapsed + 1e-9) / duration))
                self._phase_started += cycles * duration
                self._loop_repeats += cycles
                return
            self._terminal_at = now
            return

    def _pose(self, now: float) -> MotionPose:
        spec = self.specs[self.state]
        first, end = self._bounds()
        if self._terminal_at is not None:
            index = end - 1
        else:
            offset = int(max(0.0, now - self._phase_started) * spec.fps + 1e-8)
            index = min(end - 1, first + offset)
        weight = 0.0
        if self.state == "walk" and self._terminal_at is None:
            movement = spec.movement_range or spec.play_range
            start, stop = movement
            if start <= index < stop:
                ramp = max(1.0, min(spec.fps * 0.18, (stop - start) / 2.0))
                weight = min(1.0, (index - start + 1) / ramp, (stop - index) / ramp)
        blend_from = None
        blend_amount = 0.0
        count = spec.loop_blend_frames
        if (self.phase == "loop" and count and self._loop_repeats > 0
                and self._terminal_at is None and index < first + count):
            blend_from = end - 1
            progress = (index - first + 1) / (count + 1)
            blend_amount = progress * progress * (3.0 - 2.0 * progress)
        return MotionPose(self.state, self.phase, index, weight, blend_from, blend_amount)
