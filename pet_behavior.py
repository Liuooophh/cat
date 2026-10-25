"""Quiet, low-frequency cat actions and user interaction timing."""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PetAction:
    state: str
    dialogue: str
    duration: float


# Keep legacy state names available for callers and older settings.  Only the
# five states backed by the current cat clips are started by this manager.
STATE_PRIORITY = {
    "idle": 0, "walk": 1, "eat": 2, "groom": 2, "wave": 2, "interaction": 2,
    "surprised": 3, "confused": 3, "happy": 4, "laugh": 4,
    "yawn": 5, "click": 6, "clicked": 6, "angry": 7, "sleep": 8,
}

STATE_DURATION = {
    "walk": (4.0, 8.0),  # Legacy configuration key; no longer scheduled.
    "click": (4.0, 4.0), "eat": (10.0, 10.0), "groom": (10.0, 10.0),
    "sleep": (60.0, 180.0), "idle": (1.0, 1.0),
    "happy": (2.0, 3.0), "angry": (2.0, 3.0), "laugh": (2.0, 3.0),
    "yawn": (2.0, 3.0), "clicked": (1.5, 2.5),
    "surprised": (2.0, 3.0), "confused": (2.0, 3.0),
    "wave": (2.0, 3.0), "interaction": (2.0, 3.0),
}


class PetBehaviorManager:
    """Own state lifetimes while reserving activity tracking for the user.

    ``state_duration`` can be overridden with seconds or a (low, high) range.
    In particular, grooming should match the full, one-shot source clip.
    """

    def __init__(self, state_duration: Mapping[str, float | tuple[float, float]] | None = None,
                 *, manual_walk_duration: float | None = None) -> None:
        now = time.monotonic()
        self.state_duration = dict(STATE_DURATION)
        if state_duration is not None:
            self.state_duration.update(state_duration)
        # Retain the constructor keyword while older settings/builds migrate;
        # walk is no longer selected or accepted as a current cat action.
        self.manual_walk_duration = manual_walk_duration
        self.state = "idle"
        self.state_started = now
        self.state_until: float | None = 0.0
        self.last_activity = now
        self.next_autonomous_check = now + random.uniform(30.0, 70.0)
        self.sleep_after = random.uniform(180.0, 300.0)

    def _duration(self, state: str) -> float:
        duration = self.state_duration.get(state, (2.0, 3.0))
        if isinstance(duration, (int, float)):
            return float(duration)
        low, high = duration
        return random.uniform(low, high)

    def _start(self, state: str, dialogue: str, now: float,
               *, user_action: bool = False, duration: float | None = None) -> PetAction | None:
        if (state != "idle" and self.state != "idle"
                and (self.state_until is None or now < self.state_until)):
            current_priority = STATE_PRIORITY.get(self.state, 0)
            next_priority = STATE_PRIORITY.get(state, 0)
            if not user_action and next_priority <= current_priority:
                return None
        seconds = self._duration(state) if duration is None else duration
        if state == "sleep":
            seconds = float("inf")
        self.state = state
        self.state_started = now
        self.state_until = None if state == "sleep" else (
            now + seconds if state != "idle" else 0.0)
        if user_action:
            self.mark_activity(now=now)
        return PetAction(state, dialogue, seconds)

    def manual_action(self, state: str, *, now: float | None = None) -> PetAction:
        """Request one complete gesture, or sleep until an explicit wake.

        The UI queues requests made during an exit.  Click, eating and grooming
        play once; sleep has no deadline and therefore needs an explicit wake.
        """
        if state not in {"idle", "click", "eat", "groom", "sleep", "wake"}:
            raise ValueError(f"unsupported manual action: {state}")
        now = time.monotonic() if now is None else now
        if state == "wake":
            state = "idle"
        dialogue = "wake" if state == "idle" and self.state == "sleep" else ""
        if state == "click":
            dialogue = "clicked"
        return self._start(state, dialogue, now, user_action=True)

    def click(self, *, now: float | None = None) -> PetAction | None:
        now = time.monotonic() if now is None else now
        self.mark_activity(now=now)

        # A sleeping cat wakes on touch.  Other gestures finish undisturbed so
        # repeated presses do not make the animation jump or restart.
        if self.state == "sleep":
            return self._start("idle", "wake", now, user_action=True)
        if self.state != "idle":
            return None
        return self._start("click", "clicked", now, user_action=True)

    def user_wave(self, *, now: float | None = None) -> PetAction:
        now = time.monotonic() if now is None else now
        return self._start("idle", "wave", now, user_action=True) or PetAction("idle", "wave", 2.0)

    def context_action(self, state: str, dialogue: str, *, now: float | None = None,
                       duration: float = 3.5) -> PetAction | None:
        """Allow a brief context line only while idle; it is not user activity."""
        now = time.monotonic() if now is None else now
        if self.state != "idle":
            return None
        return self._start(state, dialogue, now, duration=duration)

    def mark_activity(self, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.last_activity = now
        self.next_autonomous_check = max(
            self.next_autonomous_check, now + random.uniform(30.0, 70.0),
        )

    def shift_time(self, seconds: float) -> None:
        """Freeze behavior deadlines while the pet is hidden in the tray."""
        self.state_started += seconds
        self.last_activity += seconds
        self.next_autonomous_check += seconds
        if self.state_until:
            self.state_until += seconds

    def update(self, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if (self.state != "idle" and self.state_until is not None
                and now >= self.state_until):
            self.state = "idle"
            self.state_started = now
            self.state_until = 0.0
            self.next_autonomous_check = now + random.uniform(30.0, 70.0)
            return True
        return False

    def autonomous_action(self, *, now: float | None = None) -> PetAction | None:
        now = time.monotonic() if now is None else now
        if self.state != "idle" or now < self.next_autonomous_check:
            return None
        self.next_autonomous_check = now + random.uniform(30.0, 70.0)

        inactive_for = now - self.last_activity
        if inactive_for >= self.sleep_after and random.random() < 0.65:
            return self._start("sleep", "", now)

        # Most checks do nothing.  Grooming is a little more common than eating;
        # both play once and neither delays the next natural nap.
        roll = random.random()
        if roll < 0.08:
            return self._start("groom", "", now)
        if roll < 0.12:
            return self._start("eat", "", now)
        return None
