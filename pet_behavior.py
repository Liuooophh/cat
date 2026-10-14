"""Low-frequency autonomous actions and click escalation for the desktop bear."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PetAction:
    state: str
    dialogue: str
    duration: float


STATE_PRIORITY = {
    "idle": 0, "walk": 1, "wave": 2, "interaction": 2,
    "surprised": 3, "confused": 3, "happy": 4, "laugh": 4,
    "yawn": 5, "clicked": 6, "angry": 7, "sleep": 8,
}

STATE_DURATION = {
    "walk": (6.0, 11.0), "happy": (2.5, 4.0), "angry": (3.0, 4.5),
    "laugh": (2.8, 4.5), "yawn": (2.8, 4.0), "sleep": (11.0, 17.0),
    "clicked": (1.8, 2.8), "surprised": (2.0, 3.2),
    "confused": (2.5, 4.0), "wave": (2.5, 4.0),
    "interaction": (3.0, 4.5), "idle": (1.0, 1.0),
}


class PetBehaviorManager:
    """Owns state lifetimes, priorities, click counts and ambient action timing."""

    def __init__(self) -> None:
        now = time.monotonic()
        self.state = "idle"
        self.state_started = now
        self.state_until = 0.0
        self.last_activity = now
        self.last_click = 0.0
        self.click_count = 0
        self.click_cooldown_until = 0.0
        self.next_autonomous_check = now + random.uniform(15.0, 45.0)

    @staticmethod
    def _duration(state: str) -> float:
        low, high = STATE_DURATION.get(state, (2.5, 4.0))
        return random.uniform(low, high)

    def _start(self, state: str, dialogue: str, now: float,
               *, user_action: bool = False, duration: float | None = None) -> PetAction | None:
        if state != "idle" and self.state != "idle" and now < self.state_until:
            current_priority = STATE_PRIORITY.get(self.state, 0)
            next_priority = STATE_PRIORITY.get(state, 0)
            if not user_action and next_priority <= current_priority:
                return None
        self.state = state
        self.state_started = now
        self.state_until = now + (self._duration(state) if duration is None else duration)
        self.last_activity = now
        return PetAction(state, dialogue, self.state_until - now)

    def click(self, *, now: float | None = None) -> PetAction | None:
        now = time.monotonic() if now is None else now
        self.last_activity = now
        if now - self.last_click < 1.5:
            self.click_count += 1
        else:
            self.click_count = 1
        self.last_click = now

        if now < self.click_cooldown_until:
            return None
        if self.click_count >= 5:
            self.click_cooldown_until = now + 5.0
            self.click_count = 0
            return self._start("angry", "angry", now, user_action=True)
        if self.click_count >= 3:
            if random.random() > 0.86:
                return None
            state = "angry" if random.random() < 0.55 else "clicked"
            return self._start(state, "clicked", now, user_action=True)
        if random.random() > 0.72:
            return None
        return self._start("clicked", "clicked", now, user_action=True)

    def user_wave(self, *, now: float | None = None) -> PetAction:
        now = time.monotonic() if now is None else now
        self.click_count = 0
        return self._start("wave", "wave", now, user_action=True) or PetAction("wave", "wave", 2.8)

    def context_action(self, state: str, dialogue: str, *, now: float | None = None,
                       duration: float = 3.5) -> PetAction | None:
        """Start a low-priority reaction to time, weather, or local CPU load."""
        now = time.monotonic() if now is None else now
        if self.state != "idle":
            return None
        return self._start(state, dialogue, now, duration=duration)

    def mark_activity(self, *, now: float | None = None) -> None:
        self.last_activity = time.monotonic() if now is None else now

    def update(self, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.state != "idle" and now >= self.state_until:
            self.state = "idle"
            self.state_started = now
            self.state_until = 0.0
            self.next_autonomous_check = now + random.uniform(15.0, 45.0)
            return True
        if self.click_count and now - self.last_click >= 4.0:
            self.click_count = 0
        return False

    def autonomous_action(self, *, now: float | None = None) -> PetAction | None:
        now = time.monotonic() if now is None else now
        if self.state != "idle" or now < self.next_autonomous_check:
            return None
        self.next_autonomous_check = now + random.uniform(15.0, 45.0)

        inactive_for = now - self.last_activity
        if inactive_for >= 180.0 and random.random() < 0.30:
            return self._start("sleep", "sleep", now)

        # These are per-check chances, leaving many checks quiet so the pet
        # feels settled instead of firing a fixed-timer animation loop.
        roll = random.random()
        chances = [
            (0.10, "idle", "idle"),
            (0.06, "yawn", "yawn"),
            (0.04, "happy", "happy"),
            (0.025, "laugh", "laugh"),
            (0.04, "confused", "confused"),
            (0.02, "surprised", "surprised"),
            (0.025, "interaction", "interaction"),
            (0.015, "wave", "wave"),
            (0.01, "sleep", "sleep"),
            (0.025, "walk", "idle"),
        ]
        cumulative = 0.0
        for chance, state, dialogue in chances:
            cumulative += chance
            if roll < cumulative:
                return self._start(state, dialogue, now)
        return None
