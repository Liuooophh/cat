"""Background system metrics and foreground application's displayed FPS.

FPS comes from Intel's PresentMon ETW consumer, never from this pet's timer,
monitor refresh rate, or DWM. No elevation, service, or driver is installed.
"""

from __future__ import annotations

import csv
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path

from pet_context import CpuMonitor
from pet_temperature import LhmTemperatureReader
from pet_temperature_models import DeviceTemperature


@dataclass(frozen=True)
class MetricsSnapshot:
    cpu_percent: float | None = None
    cpu_status: str = "starting"
    cpu_source: str | None = None
    cpu_error: str = ""
    cpu_diagnostics: str = ""
    fps: float | None = None
    fps_target: str | None = None
    fps_status: str = "starting"
    cpu_temperature: float | None = None
    temperature_status: str = "starting"
    temperature_source: str | None = None
    cpu_temperatures: tuple[DeviceTemperature, ...] = ()
    gpu_temperatures: tuple[DeviceTemperature, ...] = ()
    temperature_error: str = ""
    temperature_runtime: str = ""
    temperature_diagnostics: str = ""
    temperature_updated_at: float = 0.0
    updated_at: float = 0.0
    fps_updated_at: float = 0.0


@dataclass(frozen=True)
class ForegroundTarget:
    pid: int
    name: str
    process_ids: frozenset[int]


class _FrameWindow:
    """A displayed-frame window, keeping swap chains separate to avoid double counting."""

    def __init__(self, duration: float = 2.0) -> None:
        self.duration = duration
        self.streams: dict[tuple[int, str], deque[tuple[float, float]]] = {}

    def clear(self) -> None:
        self.streams.clear()

    def add(self, row: dict[str, str], now: float) -> bool:
        try:
            if int(row.get("Dropped", "1")) != 0:
                return False
            interval = float(row["msBetweenDisplayChange"])
            if not math.isfinite(interval) or not 0 < interval <= 10_000:
                return False
            key = int(row["ProcessID"]), row["SwapChainAddress"]
        except (ValueError, KeyError, TypeError, OverflowError):
            return False
        if key not in self.streams:
            if len(self.streams) >= 64:
                oldest = min(self.streams, key=lambda stream: self.streams[stream][-1][0])
                del self.streams[oldest]
            self.streams[key] = deque(maxlen=4096)
        self.streams[key].append((now, interval))
        return True

    def value(self, now: float, process_ids: frozenset[int]) -> tuple[float | None, float]:
        candidates = []
        for key, frames in list(self.streams.items()):
            while frames and frames[0][0] < now - self.duration:
                frames.popleft()
            if not frames:
                del self.streams[key]
                continue
            if key[0] in process_ids and len(frames) >= 2 and now - frames[-1][0] < 1.5:
                # One application's several windows/swap chains must not be added together.
                candidates.append(frames)
        if not candidates:
            return None, 0.0
        main_stream = max(candidates, key=len)
        milliseconds = sum(frame[1] for frame in main_stream)
        return 1000.0 * len(main_stream) / milliseconds, main_stream[-1][0]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _process_table() -> dict[int, tuple[int, str]]:
    if os.name != "nt":
        return {}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        return {}
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    table = {}
    try:
        valid = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while valid:
            table[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), str(entry.szExeFile))
            valid = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return table


def _foreground_target(table: dict[int, tuple[int, str]], own_pid: int) -> tuple[ForegroundTarget | None, str]:
    if os.name != "nt":
        return None, "unavailable"
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    window = user.GetForegroundWindow()
    if not window:
        return None, "no_foreground"
    pid_value = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid_value))
    pid = int(pid_value.value)
    return _target_from_pid(pid, table, own_pid)


def _target_from_pid(pid: int, table: dict[int, tuple[int, str]], own_pid: int) -> tuple[ForegroundTarget | None, str]:
    if not pid:
        return None, "no_foreground"
    own_parent, own_name = table.get(own_pid, (0, ""))
    if pid == own_pid or (pid == own_parent and own_name and table.get(pid, (0, ""))[1] == own_name):
        return None, "self_foreground"
    name = table.get(pid, (0, ""))[1]
    if not name or name.lower() in {"dwm.exe", "explorer.exe", "shellexperiencehost.exe", "lockapp.exe"}:
        return None, "no_foreground"
    members = {pid}
    # Browsers/Electron render in same-name child GPU processes. Never borrow DWM data.
    changed = True
    while changed:
        changed = False
        for child, (parent, child_name) in table.items():
            if parent in members and child not in members and child_name.lower() == name.lower():
                members.add(child)
                changed = True
    return ForegroundTarget(pid, name, frozenset(members)), "starting"






class MetricsMonitor:
    """Three independent workers; snapshot/configure never perform I/O on Tk's thread."""

    def __init__(self, vendor_dir: str | os.PathLike[str] | None = None, *, sample_interval: float = 1.0,
                 temperature_reader_factory=None) -> None:
        root = Path(vendor_dir) if vendor_dir is not None else Path(__file__).resolve().parent / "assets" / "vendor"
        self._presentmon = root / "presentmon" / "PresentMon-2.6.0-x64.exe"
        if root.name == "presentmon":
            self._presentmon = root / "PresentMon-2.6.0-x64.exe"
        self._guardian = self._presentmon.with_name("PawpalMetricsGuard.exe")
        temperature_root = root.parent if root.name == "presentmon" else root
        self._temperature_reader_factory = (temperature_reader_factory or
            (lambda: LhmTemperatureReader(vendor_dir=temperature_root)))
        self._interval = max(0.25, float(sample_interval))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._snapshot = MetricsSnapshot()
        self._fps_enabled = True
        self._temperature_enabled = True
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._session = "Pawpal-" + str(os.getpid()) + "-" + uuid.uuid4().hex[:10]
        self._frames = _FrameWindow()
        self._target: ForegroundTarget | None = None
        self._capture_status = "starting"

    def start(self) -> None:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                return
            self._stop.clear()
            self._threads = [threading.Thread(target=target, name=name, daemon=True) for target, name in (
                (self._cpu_worker, "Pawpal-system-CPU"), (self._fps_worker, "Pawpal-PresentMon"),
                (self._temperature_worker, "Pawpal-CPU-temperature"))]
            for thread in self._threads:
                thread.start()

    def configure(self, *, fps_enabled: bool = True, temperature_enabled: bool = True) -> None:
        with self._lock:
            self._fps_enabled = bool(fps_enabled)
            self._temperature_enabled = bool(temperature_enabled)
            if not self._fps_enabled:
                self._snapshot = replace(self._snapshot, fps=None, fps_target=None, fps_status="disabled", fps_updated_at=0.0)
            if not self._temperature_enabled:
                self._snapshot = replace(self._snapshot, cpu_temperature=None, temperature_source=None,
                    cpu_temperatures=(), gpu_temperatures=(), temperature_status="disabled")
        self._wake.set()

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return self._snapshot

    def _publish(self, **values: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **values)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        # The worker closes its own session. Join from application teardown, never a frame tick.
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=5)

    def _cpu_worker(self) -> None:
        monitor = CpuMonitor()
        try:
            while not self._stop.is_set():
                try:
                    value = monitor.sample()
                    self._publish(cpu_percent=value, cpu_status=monitor.status,
                                  cpu_source=monitor.source, cpu_error=monitor.last_error,
                                  cpu_diagnostics=json.dumps(monitor.diagnostics, ensure_ascii=False, allow_nan=False),
                                  updated_at=time.monotonic())
                except Exception as exc:
                    self._publish(cpu_percent=None, cpu_status="unavailable", cpu_source=None,
                                  cpu_error=f"{type(exc).__name__}: {exc}", updated_at=time.monotonic())
                self._stop.wait(self._interval)
        finally:
            monitor.close()

    def _temperature_worker(self) -> None:
        reader = None
        try:
            while not self._stop.is_set():
                with self._lock:
                    enabled = self._temperature_enabled
                if not enabled:
                    if reader is not None:
                        closing = reader
                        reader = None
                        try:
                            closing.close()
                        except Exception as exc:
                            self._publish(temperature_error=f"Temperature cleanup failed: {type(exc).__name__}: {exc}")
                else:
                    try:
                        if reader is None:
                            reader = self._temperature_reader_factory()
                        result = reader.read()
                        valid_cpus = [device for device in result.temperatures.cpus if device.temperature is not None]
                        representative = max(valid_cpus, key=lambda device: device.temperature) if valid_cpus else None
                        with self._lock:
                            if self._temperature_enabled:
                                self._publish(cpu_temperature=representative.temperature if representative else None,
                                    temperature_source=(f"{representative.name}: {representative.source}" if representative else None),
                                    cpu_temperatures=result.temperatures.cpus, gpu_temperatures=result.temperatures.gpus,
                                    temperature_status=result.status, temperature_error=result.error,
                                    temperature_runtime=result.runtime, temperature_diagnostics=result.diagnostic_json,
                                    temperature_updated_at=result.temperatures.sampled_at)
                    except Exception as exc:
                        # Hardware faults stay isolated from Tk, CPU and foreground FPS.
                        with self._lock:
                            if self._temperature_enabled:
                                self._publish(cpu_temperature=None, temperature_source=None,
                                              cpu_temperatures=(), gpu_temperatures=(), temperature_status="read_failed",
                                              temperature_error=f"{type(exc).__name__}: {exc}")
                self._stop.wait(1.5)
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception as exc:
                    self._publish(temperature_error=f"Temperature cleanup failed: {type(exc).__name__}: {exc}")

    def _start_capture(self) -> None:
        if os.name != "nt":
            self._capture_status = "unavailable"
            return
        if not self._presentmon.is_file() or not self._guardian.is_file():
            self._capture_status = "dependency_missing"
            return
        command = [str(self._guardian), str(os.getpid()), str(self._presentmon), self._session]
        try:
            self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, encoding="utf-8", errors="replace", bufsize=1,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                             cwd=str(self._presentmon.parent))
        except OSError:
            self._capture_status = "unavailable"
            self._process = None
            return
        self._capture_status = "starting"
        self._reader = threading.Thread(target=self._read_capture, args=(self._process,), name="Pawpal-frame-reader", daemon=True)
        self._reader.start()

    def _read_capture(self, process: subprocess.Popen[str]) -> None:
        header: list[str] | None = None
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if self._stop.is_set():
                    break
                line = line.strip().lstrip("\ufeff")
                lower = line.lower()
                if "error" in lower and any(word in lower for word in ("access", "privilege", "denied", "0x00000005")):
                    self._capture_status = "permission_denied"
                if line.startswith("Application,ProcessID,"):
                    header = next(csv.reader([line]))
                    self._capture_status = "ready"
                    continue
                if not header or not line or line.startswith(("warning", "error")):
                    continue
                values = next(csv.reader([line]))
                if len(values) != len(header):
                    continue
                row = dict(zip(header, values))
                try:
                    pid = int(row.get("ProcessID", "0"))
                except ValueError:
                    continue
                with self._lock:
                    if self._target and pid in self._target.process_ids:
                        self._frames.add(row, time.monotonic())
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()
            if self._capture_status != "permission_denied":
                self._capture_status = "unavailable"

    def _stop_capture(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                subprocess.run([str(self._presentmon), "--session_name", self._session, "--terminate_existing_session"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=str(self._presentmon.parent))
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        self._reader = None
        with self._lock:
            self._frames.clear()

    def _fps_worker(self) -> None:
        table: dict[int, tuple[int, str]] = {}
        next_table = 0.0
        retry_at = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                with self._lock:
                    enabled = self._fps_enabled
                if not enabled:
                    self._stop_capture()
                    self._publish(fps=None, fps_target=None, fps_status="disabled", fps_updated_at=0.0)
                else:
                    if now >= next_table:
                        table = _process_table()
                        next_table = now + 2.0
                    target, status = _foreground_target(table, os.getpid())
                    with self._lock:
                        if target != self._target:
                            self._frames.clear()
                            self._target = target
                    if self._process is not None and self._process.poll() is not None:
                        self._stop_capture()
                        retry_at = now + 15.0
                    if self._process is None and now >= retry_at:
                        self._start_capture()
                        retry_at = now + 15.0
                    if target is not None:
                        with self._lock:
                            fps, sampled_at = self._frames.value(now, target.process_ids)
                        if fps is not None:
                            status = "ok"
                        elif self._capture_status == "ready":
                            status = "not_rendering"
                        else:
                            status = self._capture_status
                        self._publish(fps=fps, fps_target=target.name, fps_status=status, fps_updated_at=sampled_at)
                    else:
                        self._publish(fps=None, fps_target=None, fps_status=status, fps_updated_at=0.0)
                self._wake.wait(0.5)
                self._wake.clear()
        finally:
            self._stop_capture()
            self._publish(fps=None, fps_status="stopped", fps_updated_at=0.0)
