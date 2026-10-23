"""Temperature formatting and a small optional numeric Windows tray icon."""
from __future__ import annotations

from functools import lru_cache
import math

from PIL import Image, ImageDraw, ImageFont

from pet_i18n import tr


def _temperature(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number != 0 and -50 <= number <= 150 else None


def _value(value):
    number = _temperature(value)
    return "--°C" if number is None else f"{number:.0f}°C"


def _short(name, limit):
    name = " ".join(str(name).split())
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _source_label(device, prefix):
    source = device.source or ""
    labels = {"cpu_package": "Package", "cpu_tdie": "Tdie", "cpu_tctl_tdie": "Tctl/Tdie",
              "cpu_tctl": "Tctl", "cpu_die_average": "Die Avg", "cpu_die": "Die",
              "cpu_core_max": "Core Max", "cpu_core_average": "Core Avg", "cpu_ccd_max": "CCD Max",
              "gpu_core": "Core", "gpu_temperature": "Temp", "cpu_temperature": "Temp"}
    label = labels.get(source, labels.get(source.removesuffix("_max"), ""))
    if source.endswith("_max") and label and not label.endswith("Max"):
        label += " Max"
    return prefix + (" " + label if label else "")


def temperature_lines(snapshot, language="zh-CN", *, compact=False):
    """Retain every device in menus, with unambiguous sources and missing values."""
    lines = []
    for kind, attribute in (("CPU", "cpu_temperatures"), ("GPU", "gpu_temperatures")):
        devices = tuple(getattr(snapshot, attribute, ()))
        if not devices:
            value = getattr(snapshot, "cpu_temperature", None) if kind == "CPU" else None
            lines.append(f"{kind}: {_value(value)}")
            continue
        for index, device in enumerate(devices, 1):
            prefix = kind + (str(index) if len(devices) > 1 else "")
            label = _source_label(device, prefix)
            name = "" if compact else " · " + _short(device.name, 60)
            missing = ""
            if _temperature(device.temperature) is None:
                reason = device.reason if device.reason in {"driver_missing", "permission_denied",
                    "no_temperature_sensors", "no_valid_temperature_readings", "driver_busy", "driver_unavailable"} else "unavailable"
                missing = " · " + tr("tray.reason." + reason, language)
            lines.append(f"{label}: {_value(device.temperature)}{name}{missing}")
    return lines


def _utf16_limit(text, length=127):
    encoded = text.encode("utf-16-le")
    if len(encoded) <= length * 2:
        return text
    return encoded[:(length - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def temperature_tooltip(snapshot, language="zh-CN", enabled=True):
    name = tr("app_name", language)
    if not enabled:
        return name
    return _utf16_limit(name + "\n" + "\n".join(temperature_lines(snapshot, language, compact=True)))


def representative_values(snapshot):
    """The two-line icon uses the maximum valid CPU/GPU; menus retain all devices."""
    result = []
    for kind in ("gpu", "cpu"):
        values = [_temperature(device.temperature) for device in getattr(snapshot, kind + "_temperatures", ())]
        values = [value for value in values if value is not None]
        if not values and kind == "cpu" and not getattr(snapshot, "cpu_temperatures", ()):
            fallback = _temperature(getattr(snapshot, "cpu_temperature", None))
            if fallback is not None:
                values.append(fallback)
        result.append(round(max(values)) if values else None)
    return tuple(result)


@lru_cache(maxsize=96)
def temperature_icon(gpu_value, cpu_value):
    """Top green = GPU, lower amber = CPU; '--' is an unavailable reading."""
    scale = 3
    frame = Image.new("RGBA", (32 * scale, 32 * scale))
    draw = ImageDraw.Draw(frame)
    for number, value in enumerate((gpu_value, cpu_value)):
        text = "--" if value is None else str(value)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", (15 if len(text) <= 3 else 12) * scale)
        except OSError:
            font = ImageFont.load_default(size=14 * scale)
        color = (25, 145, 102, 255) if number == 0 else (192, 126, 24, 255)
        if value is None:
            color = (115, 125, 140, 255)
        draw.text((16 * scale, (8 + number * 16) * scale), text, font=font, anchor="mm", fill=color,
                  stroke_width=scale, stroke_fill=(248, 250, 253, 245))
    return frame.resize((32, 32), Image.Resampling.LANCZOS)
