"""Explicit per-user startup setup; importing this module has no side effects."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
APPROVED32_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"
APP_VALUE = "PawpalCat"
INSTALL_MARKER = "pawpal-installed.marker"
INSTALL_MARKER_CONTENT = "PawpalCat installed layout v1"


class WindowsRegistry:
    """Only accesses the current user's Run and StartupApproved records."""

    @staticmethod
    def read(path: str, name: str):
        if os.name != "nt":
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as handle:
                return winreg.QueryValueEx(handle, name)[0]
        except FileNotFoundError:
            return None

    @staticmethod
    def write(path: str, name: str, value: str) -> None:
        if os.name != "nt":
            raise OSError("Windows startup is unavailable on this platform")
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    @staticmethod
    def delete(path: str, name: str) -> None:
        if os.name != "nt":
            return
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class StartupStatus:
    enabled: bool
    registered: bool
    disabled_by_windows: bool
    path: Path
    error: str | None = None


class StartupManager:
    def __init__(self, executable: str | Path | None = None,
                 data_root: str | Path | None = None, registry=None,
                 *, frozen: bool | None = None, bundle_kind: str | None = None) -> None:
        self.executable = Path(executable or sys.executable).resolve()
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
        self.data_root = (Path(data_root) if data_root is not None else base / "PawpalCat").resolve()
        self._source_internal = self.executable.parent / "_internal"
        if bundle_kind is None:
            extracted = getattr(sys, "_MEIPASS", None)
            bundle_kind = ("onedir" if extracted is not None and
                           Path(extracted).resolve() == self._source_internal.resolve() else "onefile")
        if bundle_kind not in {"onedir", "onefile"}:
            raise ValueError("bundle_kind must be onedir or onefile")
        self.bundle_kind = bundle_kind
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        try:
            self.installed = (self.frozen and self.executable.name.lower() == "pawpalcat.exe" and
                              self.executable.with_name(INSTALL_MARKER).read_text(encoding="utf-8").strip() == INSTALL_MARKER_CONTENT)
        except (OSError, UnicodeError):
            self.installed = False
        # Separate layouts keep a onefile upgrade away from old onedir DLLs.
        # Legacy app/PawpalCat.exe and unknown files are left untouched.
        self._managed_root = self.data_root / "app"
        self.stable_path = self.executable if self.installed else self._managed_root / bundle_kind / "PawpalCat.exe"
        self.registry = registry if registry is not None else WindowsRegistry()

    def _managed_destination(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._managed_root):
            raise OSError("Startup destination escapes the managed app directory")
        return resolved

    def _bundle_error(self) -> str | None:
        if self.installed:
            if not self.executable.is_file():
                return "Installed executable is missing"
            if self.bundle_kind == "onedir":
                required = (f"python{sys.version_info.major}{sys.version_info.minor}.dll", "base_library.zip")
                if any(not (self._source_internal / name).is_file() for name in required):
                    return "Installed Python dependencies are missing"
            return None
        self._managed_destination(self.stable_path)
        if not self.stable_path.is_file():
            return "Startup executable is missing"
        if self.bundle_kind == "onefile":
            return None
        inventory_path = self.stable_path.with_name("startup-bundle.json")
        self._managed_destination(inventory_path)
        try:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            if inventory.get("schema_version") != 1 or not inventory.get("files"):
                return "Startup bundle inventory is invalid"
            for item in inventory["files"]:
                relative = Path(item["path"])
                if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "_internal":
                    return "Startup bundle dependency path is invalid"
                target = self._managed_destination(self.stable_path.parent / relative)
                if not target.is_file() or target.stat().st_size != item["size"]:
                    return f"Startup bundle dependency is missing or incomplete: {relative}"
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            return f"Startup bundle inventory is missing or invalid: {exc}"
        return None

    @property
    def command(self) -> str:
        return f'"{self.stable_path.resolve()}" --autostart'

    def _windows_disabled(self) -> bool:
        for path in (APPROVED_KEY, APPROVED32_KEY):
            value = self.registry.read(path, APP_VALUE)
            if isinstance(value, bytes) and len(value) >= 4:
                # Windows uses 2/6 for enabled and 3/7 for user-disabled entries.
                if int.from_bytes(value[:4], "little") in {3, 7}:
                    return True
        return False

    def status(self) -> StartupStatus:
        try:
            registered = self.registry.read(RUN_KEY, APP_VALUE) == self.command
            disabled = self._windows_disabled()
            error = self._bundle_error() if registered else None
            return StartupStatus(registered and not disabled and error is None,
                                 registered, disabled, self.stable_path, error)
        except OSError as exc:
            return StartupStatus(False, False, False, self.stable_path, str(exc))

    def _copy_stable_executable(self) -> None:
        if not self.frozen or self.executable.suffix.lower() != ".exe":
            raise OSError("source_run")
        if not self.executable.is_file():
            raise FileNotFoundError("The packaged executable was not found")
        if self.installed:
            error = self._bundle_error()
            if error:
                raise OSError(error)
            return
        self._managed_destination(self.stable_path)
        if self.executable == self.stable_path.resolve():
            error = self._bundle_error()
            if error:
                raise OSError(error)
            return
        source_files: list[Path] = []
        if self.bundle_kind == "onedir":
            required = (f"python{sys.version_info.major}{sys.version_info.minor}.dll", "base_library.zip")
            if not self._source_internal.is_dir() or any(
                    not (self._source_internal / name).is_file() for name in required):
                raise FileNotFoundError("The onedir _internal Python dependencies are missing")
            source_root = self._source_internal.resolve()
            source_files = sorted(path for path in self._source_internal.rglob("*") if path.is_file())
            if any(not path.resolve().is_relative_to(source_root) for path in source_files):
                raise OSError("The onedir bundle contains a dependency outside its resource directory")
        self.stable_path.parent.mkdir(parents=True, exist_ok=True)
        if self.bundle_kind == "onedir":
            inventory = []
            for source in source_files:
                relative = Path("_internal") / source.relative_to(self._source_internal)
                target = self._managed_destination(self.stable_path.parent / relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_copy(source, target)
                inventory.append({"path": relative.as_posix(), "size": source.stat().st_size})
            payload = json.dumps({"schema_version": 1, "files": inventory}, ensure_ascii=False).encode("utf-8")
            self._atomic_write(self.stable_path.with_name("startup-bundle.json"), payload)
        self._atomic_copy(self.executable, self.stable_path)
        error = self._bundle_error()
        if error:
            raise OSError(error)

    def _atomic_copy(self, source: Path, target: Path) -> None:
        self._managed_destination(target)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="PawpalCat-", suffix=".tmp", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        self._managed_destination(target)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="PawpalCat-", suffix=".tmp", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def enable(self) -> StartupStatus:
        """Explicitly register the stable copy; never undo Windows disabling it."""
        previous = self.status()
        if previous.disabled_by_windows:
            return previous
        if not self.frozen:
            return StartupStatus(False, previous.registered, False, self.stable_path, "source_run")
        try:
            self._copy_stable_executable()
            self.registry.write(RUN_KEY, APP_VALUE, self.command)
            return self.status()
        except OSError as exc:
            return StartupStatus(False, previous.registered, previous.disabled_by_windows,
                                 self.stable_path, str(exc))

    def disable(self) -> StartupStatus:
        """Remove our Run value without changing StartupApproved or deleting files."""
        try:
            self.registry.delete(RUN_KEY, APP_VALUE)
            return self.status()
        except OSError as exc:
            previous = self.status()
            return StartupStatus(previous.enabled, previous.registered,
                                 previous.disabled_by_windows, self.stable_path, str(exc))


class SingleInstance:
    """Current-user Windows mutex. Call only in the executable entry point."""

    def __init__(self, name: str | None = None, *, api=None) -> None:
        identity = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        self.name = name or ("Local\\PawpalCat-" + suffix)
        self._api = api
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if self._api is None:
            if os.name != "nt":
                return True
            self._api = ctypes.WinDLL("kernel32", use_last_error=True)
            self._api.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            self._api.CreateMutexW.restype = ctypes.c_void_p
            self._api.CloseHandle.argtypes = (ctypes.c_void_p,)
            self._api.CloseHandle.restype = ctypes.c_int
        ctypes.set_last_error(0)
        handle = self._api.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            self._api.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._api.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("already_running")
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
