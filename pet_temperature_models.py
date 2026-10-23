"""Immutable hardware-temperature records and deterministic sensor selection.

This module has no Windows, CLR, or I/O dependency. The collector groups a
physical CPU/GPU and its subhardware in ``HardwareReading``; each sensor keeps
its actual parent identity for diagnostics. Values are degrees Celsius.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SensorReading:
    identifier: str
    name: str
    sensor_type: str
    value: float | None
    hardware_id: str = ""
    hardware_name: str = ""
    hardware_type: str = ""


@dataclass(frozen=True)
class HardwareReading:
    identifier: str
    name: str
    hardware_type: str
    sensors: tuple[SensorReading, ...] = ()

    def __post_init__(self) -> None:
        # Tuple conversion prevents a caller-owned list mutating a published
        # snapshot after the worker releases its lock.
        object.__setattr__(self, "sensors", tuple(self.sensors))


@dataclass(frozen=True)
class DeviceTemperature:
    identifier: str
    name: str
    hardware_type: str
    temperature: float | None
    source: str | None
    selected_sensor_ids: tuple[str, ...]
    sensors: tuple[SensorReading, ...]
    reason: str


@dataclass(frozen=True)
class TemperatureSnapshot:
    cpus: tuple[DeviceTemperature, ...] = ()
    gpus: tuple[DeviceTemperature, ...] = ()
    sampled_at: float = 0.0


def _enum_name(value: str) -> str:
    # CLR enum strings normally contain just "Cpu"/"Temperature"; accepting
    # their qualified forms also makes adapters and diagnostics unambiguous.
    return str(value).rsplit(".", 1)[-1].casefold()


def _hardware_kind(hardware_type: str) -> str | None:
    value = _enum_name(hardware_type)
    if value == "cpu":
        return "cpu"
    if value in {"gpu", "gpunvidia", "gpuamd", "gpuintel"}:
        return "gpu"
    return None


def temperature_value(value: object) -> float | None:
    """Validate an actively monitored CPU/GPU temperature reading.

    A broad physical range permits subzero cooling and hot supported devices;
    it is a corruption/sentinel filter, not a thermal safety threshold. LHM may
    convert inaccessible low-level readings into zero, so this Windows desktop
    application conservatively rejects raw zero as well. The original sensor
    record retains it for diagnostics. Unusual genuine zero-degree cooling is
    consequently unavailable rather than reported as successful monitoring.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number != 0.0 and -50.0 <= number <= 150.0 else None


def _words(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).casefold()).strip()


def _belongs_to_device(sensor: SensorReading, device: HardwareReading, kind: str) -> bool:
    actual_kind = _hardware_kind(sensor.hardware_type)
    if actual_kind is not None and actual_kind != kind:
        return False
    # Full LHM identifiers are rooted at the owner device. A subhardware path
    # is allowed; a different GPU/CPU must never leak into this device's result.
    owner = device.identifier.rstrip("/").casefold()
    actual = sensor.hardware_id.rstrip("/").casefold()
    if owner and actual and actual != owner and not actual.startswith(owner + "/"):
        return False
    return True


def _cpu_candidate(name: str) -> tuple[int, str] | None:
    text = _words(name)
    if not text:
        return None
    # A CPU device may expose auxiliary or offset sensors too. Their hotter
    # value must not win simply because it contains the word "CPU".
    if any(re.search(r"\b" + token + r"\b", text) for token in (
        "hot spot", "hotspot", "memory", "vram", "vrm", "socket", "soc",
        "distance", "tjmax", "tjunction", "limit", "offset")):
        return None
    if re.search(r"\bpackage\b", text):
        return 0, "cpu_package"
    # AMD CCD and individual-core Tdie readings describe part of a processor,
    # unlike the unnumbered "Core (Tdie)" package representative.
    if re.search(r"\bccd\s*\d*\b|\bcore complex die\b", text):
        return 70, "cpu_ccd_max"
    if re.search(r"\bcore\s*\d+\b", text):
        return 60, "cpu_core_max"
    has_tdie = bool(re.search(r"\btdie\b", text))
    has_tctl = bool(re.search(r"\btctl\b", text))
    if has_tdie and not has_tctl:
        return 10, "cpu_tdie"
    if has_tdie and has_tctl:
        return 20, "cpu_tctl_tdie"
    if re.search(r"\bdie\b", text):
        return (25, "cpu_die_average") if re.search(r"\b(?:average|avg)\b", text) else (30, "cpu_die")
    if has_tctl:
        # Tctl can include a vendor control offset. Keep that source explicit;
        # never relabel it as Package or Tdie.
        return 40, "cpu_tctl"
    if re.search(r"\bcore\s+(?:max|maximum)\b", text):
        return 50, "cpu_core_max"
    if re.search(r"\bcore\s+(?:average|avg)\b", text):
        return 55, "cpu_core_average"
    if text in {"cpu", "cpu temperature", "cpu temp", "processor", "processor temperature"}:
        return 45, "cpu_temperature"
    if text in {"core", "cpu core"}:
        return 60, "cpu_core_max"
    return None


def _gpu_candidate(name: str) -> tuple[int, str] | None:
    text = _words(name)
    # Do not substitute these for GPU core, even if no core sensor exists.
    if any(re.search(r"\b" + token + r"\b", text) for token in (
        "hot spot", "hotspot", "junction", "memory", "vram", "vrm", "mos",
        "board", "ambient", "soc", "liquid", "water", "power", "package")):
        return None
    if text in {"gpu core", "gpu core temperature", "gpu core temp", "core", "core temperature"}:
        return 0, "gpu_core"
    # A generic GPU sensor within a known GPU device is LHM's other common
    # core-temperature label; record the weaker label rather than claiming Core.
    if text in {"gpu", "gpu temperature", "gpu temp", "graphics temperature"}:
        return 10, "gpu_temperature"
    return None


def _select_device(device: HardwareReading, kind: str) -> DeviceTemperature:
    sensors = tuple(sorted((sensor for sensor in device.sensors
                            if _enum_name(sensor.sensor_type) == "temperature"),
                           key=lambda sensor: (sensor.identifier.casefold(), sensor.name.casefold())))
    candidates: list[tuple[int, str, SensorReading, float]] = []
    classifier = _cpu_candidate if kind == "cpu" else _gpu_candidate
    for sensor in sensors:
        if not _belongs_to_device(sensor, device, kind):
            continue
        value = temperature_value(sensor.value)
        candidate = classifier(sensor.name)
        if value is not None and candidate is not None:
            candidates.append((candidate[0], candidate[1], sensor, value))
    if not candidates:
        if not sensors:
            reason = "no_temperature_sensors"
        elif not any(temperature_value(sensor.value) is not None for sensor in sensors):
            reason = "no_valid_temperature_readings"
        else:
            reason = "no_cpu_representative_sensor" if kind == "cpu" else "no_gpu_core_sensor"
        return DeviceTemperature(device.identifier, device.name, device.hardware_type,
                                 None, None, (), sensors, reason)
    priority = min(item[0] for item in candidates)
    chosen = [item for item in candidates if item[0] == priority]
    # Cores/CCDs are a documented fallback maximum, never renamed Package.
    # Multiple equal-priority package readings use a conservative maximum too.
    source = chosen[0][1]
    if len(chosen) > 1 and not source.endswith("_max"):
        source += "_max"
    return DeviceTemperature(device.identifier, device.name, device.hardware_type,
                             max(item[3] for item in chosen), source,
                             tuple(item[2].identifier for item in chosen), sensors, "selected")


def select_temperatures(devices: Iterable[HardwareReading], *, sampled_at: float = 0.0) -> TemperatureSnapshot:
    """Select one justified temperature per independent CPU/GPU device.

    Hardware type is authoritative; names alone cannot turn a board/ACPI
    sensor into a CPU or GPU. Identical product names stay separate by hardware
    identifier. This function does not invent readings or choose a global GPU.
    """
    cpus: list[DeviceTemperature] = []
    gpus: list[DeviceTemperature] = []
    for device in sorted(devices, key=lambda item: (item.identifier.casefold(), item.name.casefold())):
        kind = _hardware_kind(device.hardware_type)
        if kind == "cpu":
            cpus.append(_select_device(device, kind))
        elif kind == "gpu":
            gpus.append(_select_device(device, kind))
    return TemperatureSnapshot(tuple(cpus), tuple(gpus), float(sampled_at))
