"""Local system context and optional city weather for the desktop companion."""

from __future__ import annotations

import ctypes
import json
import os
import random
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen


WEATHER_TEXT = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "有雾", 48: "有雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪",
    77: "雪粒", 80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪", 95: "雷雨", 96: "雷雨伴冰雹", 99: "强雷雨伴冰雹",
}


@dataclass(frozen=True)
class WeatherReading:
    city: str
    temperature: float
    code: int
    condition: str

    @property
    def status_text(self) -> str:
        return f"{self.city} {self.condition} {self.temperature:.0f}°C"

    @property
    def signature(self) -> tuple[str, int, int]:
        return self.city, self.code, round(self.temperature / 4)

    @property
    def notable(self) -> bool:
        return self.code >= 51 or self.temperature >= 35 or self.temperature <= 2

    @property
    def dialogue_key(self) -> str:
        if self.code in {95, 96, 99}:
            return "weather_storm"
        if self.code in {71, 73, 75, 77, 85, 86}:
            return "weather_snow"
        if self.code >= 51:
            return "weather_rain"
        if self.temperature >= 35:
            return "weather_hot"
        if self.temperature <= 2:
            return "weather_snow"
        return "weather_rain"


def _get_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "XiongdaDesktopPet/1.0"})
    with urlopen(request, timeout=9) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("天气服务返回了无法识别的数据。")
    if data.get("error"):
        raise ValueError(str(data.get("reason") or "天气服务暂时无法查询。"))
    return data


def fetch_weather(city: str) -> WeatherReading:
    """Resolve a user-entered city and fetch current modeled conditions."""
    city = city.strip()
    if not city:
        raise ValueError("请先填写城市名称。")
    geo_query = urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
    geo = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + geo_query)
    results = geo.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError(f"没找到“{city}”，请试试城市名或“城市, 省份”。")
    location = results[0]
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("没有找到该地点的坐标，请检查城市名称。")
    weather_query = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,weather_code",
        "temperature_unit": "celsius",
        "timezone": "auto",
    })
    forecast = _get_json("https://api.open-meteo.com/v1/forecast?" + weather_query)
    current = forecast.get("current")
    if not isinstance(current, dict):
        raise ValueError("天气服务没有返回当前天气。")
    try:
        temperature = float(current["temperature_2m"])
        code = int(current["weather_code"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("天气服务返回的数据不完整。") from exc
    city_name = str(location.get("name") or city)
    condition = WEATHER_TEXT.get(code, "天气变化")
    return WeatherReading(city_name, temperature, code, condition)


def _filetime_value(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


class CpuMonitor:
    """Sample local CPU utilization from Windows without extra packages."""

    def __init__(self) -> None:
        self._get_system_times = None
        self._previous: tuple[int, int, int] | None = None
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            function = kernel32.GetSystemTimes
            function.argtypes = [
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
            ]
            function.restype = wintypes.BOOL
            self._get_system_times = function

    def sample(self) -> float | None:
        if self._get_system_times is None:
            return None
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not self._get_system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        current = (_filetime_value(idle), _filetime_value(kernel), _filetime_value(user))
        previous, self._previous = self._previous, current
        if previous is None:
            return None
        idle_delta = max(0, current[0] - previous[0])
        total_delta = max(0, current[1] - previous[1]) + max(0, current[2] - previous[2])
        if total_delta <= 0:
            return None
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


@dataclass(frozen=True)
class ContextEvent:
    state: str
    dialogue: str
    duration: float


class ContextReactionScheduler:
    """Throttle night, CPU and weather reactions so context stays unobtrusive."""

    MIN_REACTION_GAP = 900.0

    def __init__(self) -> None:
        self.cpu_busy_since: float | None = None
        self.last_cpu_reaction = 0.0
        self.last_weather_reaction = 0.0
        self.last_weather_signature: tuple[str, int, int] | None = None
        self.pending_weather = False
        self.last_night_date: object | None = None
        self.next_reaction_at = 0.0

    def reset_weather(self) -> None:
        self.last_weather_signature = None
        self.pending_weather = False

    def poll(self, *, now: float, local_time: datetime, cpu_percent: float | None,
             weather: WeatherReading | None, is_idle: bool,
             cpu_enabled: bool, night_enabled: bool) -> ContextEvent | None:
        if weather is None:
            self.reset_weather()
        elif weather.signature != self.last_weather_signature:
            self.last_weather_signature = weather.signature
            self.pending_weather = weather.notable

        if cpu_enabled and cpu_percent is not None and cpu_percent >= 85.0:
            if self.cpu_busy_since is None:
                self.cpu_busy_since = now
        else:
            self.cpu_busy_since = None

        if not is_idle or now < self.next_reaction_at:
            return None

        date_key = local_time.date()
        if night_enabled and 0 <= local_time.hour < 5 and self.last_night_date != date_key:
            self.last_night_date = date_key
            return self._event("yawn", "time", now, 3.8)

        if (cpu_enabled and self.cpu_busy_since is not None
                and now - self.cpu_busy_since >= 20.0
                and now - self.last_cpu_reaction >= 1200.0):
            self.last_cpu_reaction = now
            return self._event("confused", "cpu", now, 3.5)

        if self.pending_weather and now - self.last_weather_reaction >= 7200.0:
            self.pending_weather = False
            self.last_weather_reaction = now
            dialogue = weather.dialogue_key if weather is not None else "weather_rain"
            return self._event("interaction", dialogue, now, 3.8)
        return None

    def _event(self, state: str, dialogue: str, now: float,
               duration: float) -> ContextEvent:
        self.next_reaction_at = now + self.MIN_REACTION_GAP
        return ContextEvent(state, dialogue, duration)
