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


# Keep the original state names available for callers and older settings.
STATE_PRIORITY = {
    "idle": 0, "walk": 1, "groom": 2, "wave": 2, "interaction": 2,
    "surprised": 3, "confused": 3, "happy": 4, "laugh": 4,
    "yawn": 5, "clicked": 6, "angry": 7, "sleep": 8,
}

STATE_DURATION = {
    "walk": (4.0, 8.0), "groom": (9.1667, 9.1667),
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
        self.manual_walk_duration = manual_walk_duration
        self.state = "idle"
        self.state_started = now
        self.state_until: float | None = 0.0
        self.last_activity = now
        self.last_click = 0.0
        self.click_count = 0
        self.click_cooldown_until = 0.0
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

        The UI queues requests made during an exit. ``manual_walk_duration``
        should fall inside the first stable loop: MotionPlayer completes that
        loop and the contiguous exit after this intent expires.
        """
        if state not in {"idle", "walk", "groom", "sleep", "wake"}:
            raise ValueError(f"unsupported manual action: {state}")
        now = time.monotonic() if now is None else now
        if state == "wake":
            state = "idle"
        dialogue = "wake" if state == "idle" and self.state == "sleep" else ""
        duration = None
        if state == "walk":
            duration = self.manual_walk_duration
            if duration is None:
                configured = self.state_duration.get("walk", STATE_DURATION["walk"])
                duration = float(configured if isinstance(configured, (int, float))
                                 else configured[0])
        return self._start(state, dialogue, now, user_action=True, duration=duration)

    def click(self, *, now: float | None = None) -> PetAction | None:
        now = time.monotonic() if now is None else now
        self.mark_activity(now=now)

        # Waking is an explicit interaction, even during a previous click burst.
        if self.state == "sleep":
            self.click_count = 0
            self.last_click = now
            return self._start("idle", "wake", now, user_action=True)

        if self.click_count and now - self.last_click < 1.5:
            self.click_count += 1
        else:
            self.click_count = 1
        self.last_click = now

        if now < self.click_cooldown_until:
            return None
        if self.click_count >= 5:
            self.click_cooldown_until = now + 6.0
            self.click_count = 0
            return self._start("idle", "", now, user_action=True)

        # Touch can interrupt walking or grooming, but usually stays silent.
        dialogue = "clicked" if self.click_count == 1 and random.random() < 0.25 else ""
        if self.state == "idle" and not dialogue:
            return None
        return self._start("idle", dialogue, now, user_action=True)

    def user_wave(self, *, now: float | None = None) -> PetAction:
        now = time.monotonic() if now is None else now
        self.click_count = 0
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

    def update(self, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.click_count and now - self.last_click >= 4.0:
            self.click_count = 0
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

        # Most checks do nothing. A full groom and a short walk are occasional
        # ambient actions, neither of which should delay the next natural nap.
        roll = random.random()
        if roll < 0.08:
            return self._start("groom", "", now)
        if roll < 0.16:
            return self._start("walk", "", now)
        return None
