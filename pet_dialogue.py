"""Soft, brief responses for the cat and occasional useful context."""

from __future__ import annotations

import random
import time


# Empty banks deliberately keep ambient animation silent. Legacy categories
# remain available for existing callers, without the bear's long monologues.
DIALOGUE: dict[str, list[str]] = {
    "idle": [],
    "walk": [],
    "groom": [],
    "sleep": [],
    "happy": ["呼噜……", "喵。"],
    "angry": ["喵。", "呜……"],
    "laugh": ["呼噜……", "喵。"],
    "yawn": [],
    "clicked": ["喵。", "呼噜……"],
    "wake": ["喵。", "呼噜……"],
    "surprised": ["喵？", "嗯？"],
    "confused": ["喵？", "嗯？"],
    "wave": ["喵。", "呼噜……"],
    "interaction": ["呼噜……", "喵。"],
    "weather_rain": ["外面下雨了，记得带伞。", "下雨了。"],
    "weather_snow": ["外面有点冷，记得添衣。", "出门注意保暖。"],
    "weather_storm": ["外面有雷雨。", "雷雨天，出门留意天气。"],
    "weather_hot": ["天气热，记得喝水。", "出门留意防晒。"],
    "cpu": ["电脑正在忙。", "等电脑缓一缓。"],
    "time": ["夜深了，早点休息。", "该休息一会儿了。"],
}


COOLDOWNS = {
    "idle": 120.0, "walk": 120.0, "groom": 120.0, "sleep": 180.0,
    "happy": 45.0, "angry": 30.0, "laugh": 45.0, "yawn": 90.0,
    "clicked": 12.0, "wake": 8.0, "surprised": 45.0,
    "confused": 45.0, "wave": 30.0, "interaction": 90.0,
    "weather_rain": 7200.0, "weather_snow": 7200.0,
    "weather_storm": 7200.0, "weather_hot": 7200.0,
    "cpu": 1800.0, "time": 86400.0,
}


class DialogueManager:
    """Select brief lines without immediate repeats, honoring cooldowns."""

    def __init__(self) -> None:
        self._last_line: dict[str, str] = {}
        self._available_at: dict[str, float] = {}

    def choose(self, category: str, *, now: float | None = None,
               force: bool = False) -> tuple[str, float] | None:
        lines = DIALOGUE.get(category)
        if not lines:
            return None
        now = time.monotonic() if now is None else now
        if not force and now < self._available_at.get(category, 0.0):
            return None
        previous = self._last_line.get(category)
        choices = [line for line in lines if line != previous] or lines
        line = random.choice(choices)
        self._last_line[category] = line
        self._available_at[category] = now + COOLDOWNS.get(category, 30.0)
        duration = min(4.0, max(1.8, 1.2 + len(line) * 0.11))
        return line, duration
