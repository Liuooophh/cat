"""Whole-computer CPU usage from Windows PDH, with a time-based fallback.

Task Manager used Processor Utility on Windows 8 through earlier Windows 11,
whereas GetSystemTimes measures non-idle time. Windows 11's 2025 Task Manager
rollout changed this again; exact UI agreement is version/rollout dependent.
This app prefers the Utility counter verified on the current Windows 11 22H2
machine and records both metrics in diagnostics. Both are system metrics.
The public monitor preserves ``sample() -> float | None`` and performs no I/O
until the first sample on the metrics worker. No process CPU counter is used.
"""
from __future__ import annotations

import ctypes
import math
import os
import time
from ctypes import wintypes
from typing import Callable


UTILITY_PATH = r"\Processor Information(_Total)\% Processor Utility"
TIME_PATH = r"\Processor Information(_Total)\% Processor Time"
LEGACY_TIME_PATH = r"\Processor(_Total)\% Processor Time"
COUNTER_PATHS = (UTILITY_PATH, TIME_PATH, LEGACY_TIME_PATH)
PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000


class _PdhValueUnion(ctypes.Union):
    _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong), ("AnsiStringValue", ctypes.c_char_p),
                ("WideStringValue", ctypes.c_wchar_p)]


class _PdhFormattedValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhValueUnion)]


def _code(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def _pdh_error(operation: str, code: int) -> OSError:
    return OSError(f"{operation} failed (PDH 0x{_code(code):08X})")


def _valid_percent(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class _PdhBackend:
    """A single persistent, language-neutral query containing total counters."""

    def __init__(self, library=None) -> None:
        if library is None:
            if os.name != "nt":
                raise OSError("Windows performance counters are unavailable")
            library = ctypes.WinDLL("pdh", use_last_error=True)
        self._dll = library
        self._query = ctypes.c_void_p()
        self._counters: dict[str, ctypes.c_void_p] = {}
        self._primed = False
        self.counter_path: str | None = None
        self.raw_values: dict[str, float | None] = {}
        self.errors: dict[str, str] = {}
        # PDH_STATUS is a 32-bit LONG; query/counter handles and DWORD_PTR are
        # pointer-sized. The value structure contains an aligned native union.
        signatures = {
            "PdhOpenQueryW": [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)],
            "PdhAddEnglishCounterW": [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_void_p)],
            "PdhCollectQueryData": [ctypes.c_void_p],
            "PdhGetFormattedCounterValue": [ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD),
                                            ctypes.POINTER(_PdhFormattedValue)],
            "PdhCloseQuery": [ctypes.c_void_p],
        }
        for name, arguments in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, wintypes.LONG
        result = library.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if _code(result) != 0:
            raise _pdh_error("PdhOpenQueryW", result)
        try:
            for path in COUNTER_PATHS:
                handle = ctypes.c_void_p()
                result = library.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle))
                if _code(result) == 0 and handle.value:
                    self._counters[path] = handle
                else:
                    self.errors[path] = str(_pdh_error("PdhAddEnglishCounterW", result))
            if not self._counters:
                raise OSError("No whole-system CPU performance counter: " + "; ".join(self.errors.values()))
        except Exception:
            self.close()
            raise

    def sample(self) -> float | None:
        if not self._query.value:
            return None
        self.counter_path = None
        self.raw_values = {}
        result = self._dll.PdhCollectQueryData(self._query)
        if _code(result) != 0:
            self._primed = False
            raise _pdh_error("PdhCollectQueryData", result)
        # Rate counters require two successful collections. The worker supplies
        # the one-second interval; this method never sleeps on a GUI thread.
        if not self._primed:
            self._primed = True
            return None
        for path, handle in self._counters.items():
            value = _PdhFormattedValue()
            result = self._dll.PdhGetFormattedCounterValue(
                handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(value))
            if _code(result) != 0:
                self.errors[path] = str(_pdh_error("PdhGetFormattedCounterValue", result))
                self.raw_values[path] = None
                continue
            if _code(value.CStatus) not in (0, 1):
                self.errors[path] = f"Invalid PDH counter data (CStatus 0x{_code(value.CStatus):08X})"
                self.raw_values[path] = None
                continue
            number = _valid_percent(value.doubleValue)
            self.raw_values[path] = number
            if number is None:
                self.errors[path] = "Invalid non-finite or negative CPU counter value"
            else:
                self.errors.pop(path, None)
        for path in COUNTER_PATHS:
            number = self.raw_values.get(path)
            if number is not None:
                self.counter_path = path
                # Task Manager's display is 0–100. Retain uncapped Utility in
                # diagnostics because Turbo Boost can legitimately exceed 100.
                return min(100.0, number)
        return None

    def close(self) -> None:
        query, self._query = self._query, ctypes.c_void_p()
        self._counters.clear()
        self._primed = False
        if query.value:
            result = self._dll.PdhCloseQuery(query)
            if _code(result) != 0:
                raise _pdh_error("PdhCloseQuery", result)


def _filetime_value(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


class _SystemTimesBackend:
    """Non-idle time across all processors supported by GetSystemTimes."""

    def __init__(self, get_system_times=None, *, processor_count: int | None = None) -> None:
        self._previous: tuple[int, int, int] | None = None
        if get_system_times is None:
            if os.name != "nt":
                raise OSError("GetSystemTimes requires Windows")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_system_times = kernel.GetSystemTimes
            get_system_times.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
            get_system_times.restype = wintypes.BOOL
            if processor_count is None:
                active = kernel.GetActiveProcessorCount
                active.argtypes, active.restype = [wintypes.DWORD], wintypes.DWORD
                processor_count = int(active(0xFFFF))  # ALL_PROCESSOR_GROUPS
        if processor_count is not None and processor_count > 64:
            raise OSError("GetSystemTimes cannot represent all processor groups on this system; PDH is required")
        self._get_system_times = get_system_times

    def sample(self) -> float | None:
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not self._get_system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            self._previous = None
            raise ctypes.WinError(ctypes.get_last_error())
        current = (_filetime_value(idle), _filetime_value(kernel), _filetime_value(user))
        previous, self._previous = self._previous, current
        if previous is None:
            return None
        differences = tuple(current[index] - previous[index] for index in range(3))
        # Reset/invalid samples must not turn into fabricated 0% or 100% values.
        if any(number < 0 for number in differences):
            return None
        idle_delta, kernel_delta, user_delta = differences
        total_delta = kernel_delta + user_delta  # Kernel already includes Idle.
        if total_delta <= 0 or idle_delta > total_delta:
            return None
        return (total_delta - idle_delta) * 100.0 / total_delta

    def close(self) -> None:
        self._previous = None


class CpuMonitor:
    """Overall CPU utilization, with explicit metric sources and fallbacks.

    ``source`` is ``pdh_processor_utility``, ``pdh_processor_time`` or
    ``get_system_times``. ``last_error`` and ``diagnostics`` disclose fallback
    reasons. Counter availability varies by Windows installation; no elevation,
    service, driver or counter repair is performed.
    """

    def __init__(self, *, pdh_factory: Callable[[], object] | None = None,
                 system_times_factory: Callable[[], object] | None = None) -> None:
        self._pdh_factory = pdh_factory or _PdhBackend
        self._times_factory = system_times_factory or _SystemTimesBackend
        self._pdh = self._times = None
        self._initialized = self._closed = False
        self.source: str | None = None
        self.counter_path: str | None = None
        self.last_error = ""
        self.status = "starting"
        self.sampled_at = 0.0
        self._errors: dict[str, str] = {}

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        for name, factory in (("get_system_times", self._times_factory), ("pdh", self._pdh_factory)):
            try:
                setattr(self, "_times" if name == "get_system_times" else "_pdh", factory())
            except (OSError, AttributeError) as exc:
                self._errors[name] = f"{type(exc).__name__}: {exc}"

    def sample(self) -> float | None:
        if self._closed:
            return None
        self._initialize()
        self.source = self.counter_path = None
        fallback = None
        if self._times is not None:
            try:
                fallback = self._times.sample()
                self._errors.pop("get_system_times", None)
            except OSError as exc:
                self._errors["get_system_times"] = f"{type(exc).__name__}: {exc}"
        value = None
        if self._pdh is not None:
            try:
                value = self._pdh.sample()
                self._errors.pop("pdh", None)
            except OSError as exc:
                self._errors["pdh"] = f"{type(exc).__name__}: {exc}"
            if value is not None:
                self.counter_path = self._pdh.counter_path
                self.source = "pdh_processor_utility" if self.counter_path == UTILITY_PATH else "pdh_processor_time"
        if value is None and fallback is not None:
            value, self.source = fallback, "get_system_times"
        pdh_errors = getattr(self._pdh, "errors", {})
        self.last_error = "; ".join([*self._errors.values(), *pdh_errors.values()])
        if value is None:
            self.status = "unavailable" if self.last_error else "starting"
            return None
        self.status, self.sampled_at = "ok", time.monotonic()
        return value

    @property
    def diagnostics(self) -> dict[str, object]:
        return {"status": self.status, "source": self.source, "counter_path": self.counter_path,
                "last_error": self.last_error, "sampled_at": self.sampled_at,
                "pdh_raw_percent": dict(getattr(self._pdh, "raw_values", {})),
                "backend_errors": dict(self._errors)}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for backend in (self._pdh, self._times):
            if backend is not None:
                try:
                    backend.close()
                except OSError as exc:
                    self._errors["cleanup"] = f"{type(exc).__name__}: {exc}"
        self.source = self.counter_path = None
        self.status = "closed"
        self.last_error = "; ".join(self._errors.values())
