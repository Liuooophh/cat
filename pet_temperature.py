"""In-process LibreHardwareMonitor 0.9.6 / pythonnet temperature reader.

Only the temperature worker owns managed hardware objects. This module never
installs a driver, starts a service, elevates, or changes device permissions.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import math
import os
import struct
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from pet_temperature_models import (
    HardwareReading, SensorReading, TemperatureSnapshot, select_temperatures,
)

_CLR_LOCK = threading.RLock()
_DLL_HANDLES: list[object] = []
LIBRARY_VERSION = "0.9.6"


@dataclass(frozen=True)
class DriverAccess:
    available: bool = False
    status: str = "unsupported_platform"
    winerror: int | None = None


@dataclass(frozen=True)
class TemperatureResult:
    temperatures: TemperatureSnapshot
    status: str
    error: str = ""
    runtime: str = ""
    diagnostic_json: str = ""


class TemperatureError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def is_administrator() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def probe_pawnio() -> DriverAccess:
    """OPEN_EXISTING only: test the access LHM actually needs, never install it."""
    if os.name != "nt":
        return DriverAccess()
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Matches the pinned LHM PawnIo read/write access and READ|WRITE sharing.
    handle = kernel.CreateFileW(r"\\?\GLOBALROOT\Device\PawnIO", 0xC0000000, 3,
                                None, 3, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        status = ("driver_missing" if error in {2, 3} else
                  "permission_denied" if error == 5 else
                  "driver_busy" if error in {32, 33} else "driver_unavailable")
        return DriverAccess(False, status, error)
    kernel.CloseHandle(handle)
    return DriverAccess(True, "ok")


def default_log_dir() -> Path:
    override = os.environ.get("PAWPAL_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("APPDATA", str(Path.home()))) / "TuanziDesktopPet"
    return base / "logs"


def _logger(directory: Path, debug: bool) -> logging.Logger:
    name = "pawpal.temperature." + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:12]
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(directory / "temperature.log", maxBytes=512_000,
                                           backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


def _netfx_release() -> int:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return int(winreg.QueryValueEx(key, "Release")[0])
    except (OSError, TypeError, ValueError):
        return 0


class LhmTemperatureReader:
    def __init__(self, vendor_dir: str | os.PathLike[str] | None = None, *,
                 log_dir: str | os.PathLike[str] | None = None, debug: bool = False,
                 computer_factory: Callable[[], object] | None = None,
                 driver_probe: Callable[[], DriverAccess] = probe_pawnio) -> None:
        root = Path(vendor_dir) if vendor_dir is not None else Path(__file__).resolve().parent / "assets/vendor"
        self.asset_dir = root if root.name == "librehardwaremonitor" else root / "librehardwaremonitor"
        self._log_dir = Path(log_dir) if log_dir is not None else default_log_dir()
        self._debug = debug
        self._log: logging.Logger | None = None
        self._factory = computer_factory
        self._driver_probe = driver_probe
        self._computer = None
        self._opened = False
        self._initialized = False
        self._closed = False
        self._owner: int | None = None
        self._error_code = "starting"
        self._error = ""
        self._runtime = ""
        self._framework_release = 0
        self._driver = DriverAccess()
        self._driver_installed: bool | None = None
        self._inventory_signature = None
        self._selection_signature = None
        self._last_diagnostic: dict[str, object] = {}

    def _check_thread(self) -> None:
        owner = threading.get_ident()
        if self._owner is None:
            self._owner = owner
        elif self._owner != owner:
            raise RuntimeError("Managed temperature objects must stay on their owning worker")

    def _load_computer(self):
        if os.name != "nt":
            raise TemperatureError("unsupported_platform", "Windows is required")
        if struct.calcsize("P") != 8:
            raise TemperatureError("architecture_mismatch", "This release requires x64 Python and Windows")
        self._framework_release = _netfx_release()
        self._log.info("framework_release=%s minimum_release=528040", self._framework_release)
        if self._framework_release < 528040:
            raise TemperatureError("runtime_missing", ".NET Framework 4.8 or later is required")
        dll = self.asset_dir / "LibreHardwareMonitorLib.dll"
        if not dll.is_file():
            raise TemperatureError("dll_missing", str(dll))
        try:
            manifest = json.loads((self.asset_dir / "manifest.json").read_text(encoding="utf-8"))
            if (manifest.get("library_version") != LIBRARY_VERSION or manifest.get("runtime") != "netfx"
                    or manifest.get("target_framework") != "net472" or manifest.get("architecture") != "x64"):
                raise TemperatureError("dll_version_mismatch", "Unexpected library/runtime manifest")
            for item in manifest["files"]:
                name = item["path"]
                if Path(name).name != name:
                    raise TemperatureError("dll_load_failed", "Invalid dependency path")
                dependency = self.asset_dir / name
                if not dependency.is_file():
                    raise TemperatureError("dependency_missing", str(dependency))
                if hashlib.sha256(dependency.read_bytes()).hexdigest() != item["sha256"]:
                    raise TemperatureError("dll_load_failed", f"Dependency integrity check failed: {name}")
        except TemperatureError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TemperatureError("dependency_missing", f"Invalid or missing manifest: {exc}") from exc
        with _CLR_LOCK:
            try:
                import pythonnet
                info = pythonnet.get_runtime_info()
                if info is None:
                    pythonnet.load("netfx")
                    info = pythonnet.get_runtime_info()
                if info is None or str(info.kind).lower() not in {"netfx", ".net framework"}:
                    raise TemperatureError("runtime_incompatible", f"Expected netfx, got {info}")
                self._runtime = str(info)
                if str(self.asset_dir) not in sys.path:
                    sys.path.insert(0, str(self.asset_dir))
                if hasattr(os, "add_dll_directory"):
                    _DLL_HANDLES.append(os.add_dll_directory(str(self.asset_dir)))
                import clr
                assembly = clr.AddReference(str(dll.resolve()))
                version = str(assembly.GetName().Version)
                if not version.startswith(LIBRARY_VERSION + "."):
                    raise TemperatureError("dll_version_mismatch", f"Expected {LIBRARY_VERSION}, got {version}")
                from LibreHardwareMonitor.Hardware import Computer
                pawnio_type = assembly.GetType("LibreHardwareMonitor.PawnIo.PawnIo")
                if pawnio_type is not None:
                    installed = pawnio_type.GetProperty("IsInstalled")
                    if installed is not None:
                        self._driver_installed = bool(installed.GetValue(None))
                self._log.info("assembly_loaded path=%s version=%s runtime=%s", dll.resolve(), version, self._runtime)
                return Computer()
            except TemperatureError:
                raise
            except ImportError as exc:
                raise TemperatureError("dependency_missing", str(exc)) from exc
            except Exception as exc:
                raise TemperatureError("dll_load_failed", f"{type(exc).__name__}: {exc}") from exc

    def initialize(self) -> None:
        self._check_thread()
        if self._initialized or self._closed:
            return
        self._initialized = True
        self._log = _logger(self._log_dir, self._debug)
        self._log.info("initializing python=%s bits=%s administrator=%s log_dir=%s",
                       sys.version.split()[0], struct.calcsize("P") * 8, is_administrator(), self._log_dir)
        try:
            self._driver = self._driver_probe()
            self._log.info("pawnio_existing_device available=%s status=%s winerror=%s no_install=True no_elevation=True",
                           self._driver.available, self._driver.status, self._driver.winerror)
            self._computer = self._factory() if self._factory else self._load_computer()
            if self._factory:
                self._runtime = "test_double"
            # Only CPU/GPU. Other monitoring groups stay disabled.
            self._computer.IsCpuEnabled = True
            self._computer.IsGpuEnabled = True
            for setting in ("IsMotherboardEnabled", "IsMemoryEnabled", "IsControllerEnabled",
                            "IsNetworkEnabled", "IsStorageEnabled", "IsPsuEnabled", "IsBatteryEnabled"):
                if hasattr(self._computer, setting):
                    setattr(self._computer, setting, False)
            self._computer.Open()
            self._opened = True
            self._error_code = "ok"
            self._log.info("computer_opened cpu_enabled=True gpu_enabled=True library=%s", LIBRARY_VERSION)
        except TemperatureError as exc:
            self._error_code, self._error = exc.code, exc.detail
            self._log.error("initialization_failed code=%s detail=%s", self._error_code, self._error)
        except Exception as exc:
            self._error_code, self._error = "initialization_failed", f"{type(exc).__name__}: {exc}"
            self._log.exception("initialization_failed")

    @staticmethod
    def _value(sensor) -> float | None:
        try:
            value = sensor.Value
            return float(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

    def _collect(self) -> tuple[list[HardwareReading], list[str]]:
        devices, errors = [], []
        try:
            roots = tuple(self._computer.Hardware)
        except Exception as exc:
            return [], [f"enumeration failed: {type(exc).__name__}: {exc}"]
        for hardware in roots:
            try:
                kind = str(hardware.HardwareType)
                identifier, name = str(hardware.Identifier), str(hardware.Name)
            except Exception as exc:
                errors.append(f"device metadata failed: {type(exc).__name__}: {exc}")
                continue
            if kind.lower() not in {"cpu", "gpunvidia", "gpuamd", "gpuintel"}:
                continue
            sensors, visited = [], set()
            def update(node, depth=0):
                try:
                    key, node_name, node_kind = str(node.Identifier), str(node.Name), str(node.HardwareType)
                except Exception as exc:
                    errors.append(f"{identifier}: subhardware metadata failed: {type(exc).__name__}: {exc}")
                    return
                if key in visited or depth > 16:
                    return
                visited.add(key)
                updated = True
                try:
                    node.Update()
                except Exception as exc:
                    errors.append(f"{key}: update failed: {type(exc).__name__}: {exc}")
                    # Never publish an old value after an Update failure.
                    updated = False
                if updated:
                    try:
                        node_sensors = tuple(node.Sensors)
                    except Exception as exc:
                        errors.append(f"{key}: sensor enumeration failed: {type(exc).__name__}: {exc}")
                        node_sensors = ()
                    for sensor in node_sensors:
                        try:
                            sensor_kind = str(sensor.SensorType)
                            if sensor_kind.lower() != "temperature":
                                continue
                            sensors.append(SensorReading(str(sensor.Identifier), str(sensor.Name),
                                sensor_kind, self._value(sensor), key, node_name, node_kind))
                        except Exception as exc:
                            errors.append(f"{key}: sensor read failed: {type(exc).__name__}: {exc}")
                try:
                    children = tuple(node.SubHardware)
                except Exception as exc:
                    errors.append(f"{key}: child enumeration failed: {type(exc).__name__}: {exc}")
                    children = ()
                for child in children:
                    update(child, depth + 1)
            update(hardware)
            devices.append(HardwareReading(identifier, name, kind, tuple(sensors)))
        return devices, errors

    def read(self) -> TemperatureResult:
        self.initialize()
        now = time.time()
        devices, errors = [], []
        if self._opened and not self._closed:
            try:
                devices, errors = self._collect()
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        trusted = [replace(device, sensors=tuple(replace(sensor, value=None) for sensor in device.sensors))
                   if device.hardware_type.lower() == "cpu" and not self._driver.available else device
                   for device in devices]
        temperatures = select_temperatures(trusted, sampled_at=now)
        if not self._driver.available:
            temperatures = replace(temperatures, cpus=tuple(
                replace(device, reason=self._driver.status) for device in temperatures.cpus))
        valid = [device for device in (*temperatures.cpus, *temperatures.gpus) if device.temperature is not None]
        if self._closed:
            status = "closed"
        elif self._error_code != "ok":
            status = self._error_code
        elif valid:
            status = "ok" if len(valid) == len(temperatures.cpus) + len(temperatures.gpus) else "partial"
        elif temperatures.cpus and not self._driver.available:
            status = self._driver.status
        else:
            status = "read_failed" if errors else "no_sensors"
        inventory = [asdict(device) for device in devices]
        diagnostic = {"backend": "LibreHardwareMonitorLib + pythonnet", "library_version": LIBRARY_VERSION,
                      "dll_path": str(self.asset_dir / "LibreHardwareMonitorLib.dll"), "runtime": self._runtime,
                      "framework_release": self._framework_release,
                      "python": sys.version.split()[0], "process_bits": struct.calcsize("P") * 8,
                      "administrator": is_administrator(), "pawnio": {**asdict(self._driver), "installed": self._driver_installed},
                      "status": status, "error": self._error, "update_errors": errors,
                      "sampled_at": now, "devices": inventory,
                      "selected": asdict(temperatures), "log_file": str(self._log_dir / "temperature.log"),
                      "policy": "No installation, service start, elevation, or security changes"}
        # Strict JSON: preserve invalid raw readings as null, never NaN/Infinity.
        def safe(value):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            if isinstance(value, dict):
                return {key: safe(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [safe(item) for item in value]
            return value
        diagnostic = safe(diagnostic)
        self._last_diagnostic = diagnostic
        signature = tuple((device.identifier, tuple((sensor.identifier, sensor.name) for sensor in device.sensors))
                          for device in devices)
        if signature != self._inventory_signature:
            self._inventory_signature = signature
            self._log.info("sensor_inventory %s", json.dumps(inventory, ensure_ascii=False, default=str))
        selection = tuple((device.identifier, device.source, device.selected_sensor_ids, device.reason)
                          for device in (*temperatures.cpus, *temperatures.gpus))
        if selection != self._selection_signature:
            self._selection_signature = selection
            self._log.info("sensor_selection %s", json.dumps(diagnostic["selected"], ensure_ascii=False))
        self._log.debug("sample %s", json.dumps(diagnostic, ensure_ascii=False))
        if errors:
            signature = tuple(errors)
            if signature != getattr(self, "_last_errors", None):
                self._log.warning("hardware_update_errors %s", errors)
            self._last_errors = signature
        return TemperatureResult(temperatures, status, self._error or (errors[0] if errors else ""),
                                 self._runtime, json.dumps(diagnostic, ensure_ascii=False, allow_nan=False))

    def close(self) -> None:
        self._check_thread()
        if self._closed:
            return
        self._closed = True
        failure = None
        if self._computer is not None:
            try:
                self._computer.Close()
            except Exception as exc:
                failure = exc
                if self._log:
                    self._log.warning("computer_close_failed %s", exc)
        self._opened = False
        self._computer = None
        if self._log:
            self._log.info("computer_closed success=%s", failure is None)
        if failure is not None:
            raise TemperatureError("cleanup_failed", f"{type(failure).__name__}: {failure}") from failure
