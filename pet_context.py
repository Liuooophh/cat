"""Local system context and optional city weather for the desktop companion."""

from __future__ import annotations

import ctypes
import json
import math
import os
import random
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request
from pet_network import open_url as urlopen, network_failure, safe_detail


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

WEATHER_TEXT_EN = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Rain showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}
WEATHER_TEXT_MS = {
    0: "Cerah", 1: "Hampir cerah", 2: "Separa berawan", 3: "Mendung",
    45: "Berkabus", 48: "Kabus beku", 51: "Gerimis ringan", 53: "Gerimis",
    55: "Gerimis lebat", 56: "Gerimis beku", 57: "Gerimis beku", 61: "Hujan ringan",
    63: "Hujan", 65: "Hujan lebat", 66: "Hujan beku", 67: "Hujan beku",
    71: "Salji ringan", 73: "Salji", 75: "Salji lebat", 77: "Butiran salji",
    80: "Hujan singkat", 81: "Hujan singkat", 82: "Hujan singkat lebat",
    85: "Hujan salji", 86: "Hujan salji lebat", 95: "Ribut petir",
    96: "Ribut petir dan hujan batu", 99: "Ribut petir kuat dan hujan batu",
}


def _weather_language(language: str) -> str:
    code = str(language).lower().replace("_", "-").split("-")[0]
    return code if code in {"zh", "en", "ms"} else "zh"


_WEATHER_ERRORS = {
    "zh": {"city_required": "请先填写城市名称。", "location_not_found": "没有找到该城市，请换一个名称搜索。",
           "invalid_location": "地点坐标无效，请重新选择城市。", "network_error": "天气服务连接失败，请检查网络后重试。",
           "timeout": "天气查询超时，请稍后重试。", "service_error": "天气服务暂时不可用，请稍后重试。",
           "invalid_response": "天气服务返回的数据不完整，请稍后重试。"},
    "en": {"city_required": "Enter a city name first.", "location_not_found": "No matching city found. Try another name.",
           "invalid_location": "Invalid location coordinates. Select the city again.", "network_error": "Could not connect to the weather service. Check your connection.",
           "timeout": "The weather request timed out. Try again later.", "service_error": "The weather service is temporarily unavailable.",
           "invalid_response": "The weather service returned incomplete data."},
    "ms": {"city_required": "Masukkan nama bandar dahulu.", "location_not_found": "Bandar tidak ditemui. Cuba nama lain.",
           "invalid_location": "Koordinat lokasi tidak sah. Pilih bandar semula.", "network_error": "Tidak dapat menyambung ke perkhidmatan cuaca. Semak sambungan anda.",
           "timeout": "Permintaan cuaca tamat masa. Cuba lagi kemudian.", "service_error": "Perkhidmatan cuaca tidak tersedia buat sementara waktu.",
           "invalid_response": "Perkhidmatan cuaca mengembalikan data tidak lengkap."},
}


class WeatherError(ValueError):
    def __init__(self, code: str, language: str = "zh", detail: str = "", status: int | None = None) -> None:
        self.code = code
        self.detail = safe_detail(detail)
        self.status = status
        message = _WEATHER_ERRORS[_weather_language(language)].get(code, code)
        if status is not None:
            message += f" (HTTP {status})"
        super().__init__(message + ("\n" + self.detail if self.detail else ""))


@dataclass(frozen=True)
class WeatherLocation:
    id: int | None
    name: str
    latitude: float
    longitude: float
    country: str = ""
    admin1: str = ""
    timezone: str = ""

    @property
    def label(self) -> str:
        return " / ".join(dict.fromkeys(part for part in (self.name, self.admin1, self.country) if part))

    def to_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "WeatherLocation":
        try:
            latitude, longitude = float(value["latitude"]), float(value["longitude"])
            if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("invalid coordinates")
            name = str(value.get("name") or "").strip()
            if not name:
                raise ValueError("missing name")
            return cls(int(value["id"]) if value.get("id") is not None else None, name, latitude, longitude,
                       str(value.get("country") or ""), str(value.get("admin1") or ""), str(value.get("timezone") or ""))
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise WeatherError("invalid_location") from exc


@dataclass(frozen=True)
class WeatherReading:
    city: str
    temperature: float
    code: int
    condition: str
    observed_at: str = ""
    timezone: str = ""
    fetched_at: float = 0.0
    location: WeatherLocation | None = None
    source: str = "Open-Meteo"

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


def _get_json(url: str, language: str = "zh") -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "PawpalCat/5", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read(1_048_577)
            if len(payload) > 1_048_576:
                raise WeatherError("invalid_response", language)
            data = json.loads(payload.decode("utf-8-sig"))
    except HTTPError as exc:
        detail = urlsplit(url).hostname or "Open-Meteo"
        try:
            body = json.loads(exc.read(16_384).decode("utf-8-sig"))
            reason = body.get("reason") or body.get("message") if isinstance(body, dict) else ""
            if reason:
                detail += ": " + safe_detail(reason)
        except (OSError, ValueError, TypeError, UnicodeError):
            pass
        if exc.code == 407:
            detail = "The configured proxy requires authentication. Check its credentials."
        elif exc.code == 429:
            detail += ": request limit reached; wait before retrying."
        raise WeatherError("network_error" if exc.code == 407 else "service_error", language,
                           detail, exc.code) from exc
    except (TimeoutError, OSError, URLError) as exc:
        kind, detail = network_failure(exc)
        raise WeatherError("timeout" if kind == "timeout" else "network_error", language,
                           detail) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WeatherError("invalid_response", language) from exc
    if not isinstance(data, dict):
        raise WeatherError("invalid_response", language)
    if data.get("error"):
        raise WeatherError("service_error", language, str(data.get("reason") or ""))
    return data


def search_weather_locations(query: str, language: str = "zh") -> list[WeatherLocation]:
    """Return alternatives; callers let the user choose rather than silently taking the first match."""
    query = str(query or "").strip()
    language = _weather_language(language)
    if not query:
        raise WeatherError("city_required", language)
    geo_query = urlencode({"name": query, "count": 10, "language": language, "format": "json"})
    geo = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + geo_query, language)
    results = geo.get("results")
    if not isinstance(results, list):
        return []
    locations = []
    for value in results:
        if isinstance(value, dict):
            try:
                locations.append(WeatherLocation.from_dict(value))
            except WeatherError:
                continue
    return locations


def fetch_weather_at(location: WeatherLocation | dict[str, object], language: str = "zh") -> WeatherReading:
    """Fetch current modeled weather for the saved coordinates, without another geocoding lookup."""
    language = _weather_language(language)
    if isinstance(location, dict):
        try:
            location = WeatherLocation.from_dict(location)
        except WeatherError as exc:
            raise WeatherError("invalid_location", language) from exc
    if not isinstance(location, WeatherLocation):
        raise WeatherError("invalid_location", language)
    try:
        location = WeatherLocation.from_dict(location.to_dict())
    except WeatherError as exc:
        raise WeatherError("invalid_location", language) from exc
    weather_query = urlencode({
        "latitude": location.latitude,
        "longitude": location.longitude,
        "current": "temperature_2m,weather_code",
        "temperature_unit": "celsius",
        "timezone": "auto",
    })
    forecast = _get_json("https://api.open-meteo.com/v1/forecast?" + weather_query, language)
    current = forecast.get("current")
    if not isinstance(current, dict):
        raise WeatherError("invalid_response", language)
    try:
        temperature = float(current["temperature_2m"])
        code = int(current["weather_code"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("invalid_response", language) from exc
    if not math.isfinite(temperature) or not -100 <= temperature <= 80:
        raise WeatherError("invalid_response", language)
    conditions = {"zh": WEATHER_TEXT, "en": WEATHER_TEXT_EN, "ms": WEATHER_TEXT_MS}[language]
    condition = conditions.get(code, {"zh": "天气变化", "en": "Variable", "ms": "Berubah-ubah"}[language])
    return WeatherReading(location.name, temperature, code, condition, str(current.get("time") or ""),
                          str(forecast.get("timezone") or location.timezone), time.time(), location)


def fetch_weather(city: str, language: str = "zh") -> WeatherReading:
    """Compatibility entry point; new settings should search and save a selected location."""
    locations = search_weather_locations(city, language)
    if not locations:
        raise WeatherError("location_not_found", language)
    return fetch_weather_at(locations[0], language)


from pet_cpu import CpuMonitor


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
