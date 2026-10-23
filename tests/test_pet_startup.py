from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pet_startup import APPROVED_KEY, APP_VALUE, RUN_KEY, SingleInstance, StartupManager


class MemoryRegistry:
    def __init__(self):
        self.values = {}
        self.writes = []

    def read(self, path, name):
        return self.values.get((path, name))

    def write(self, path, name, value):
        self.values[path, name] = value
        self.writes.append((path, name, value))

    def delete(self, path, name):
        self.values.pop((path, name), None)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "Download folder" / "PawpalCat-v3.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"temporary mocked executable")
        self.registry = MemoryRegistry()
        self.manager = StartupManager(self.exe, self.root / "User data", self.registry, frozen=True)

    def test_construction_and_status_have_no_writes(self):
        self.assertFalse(self.manager.status().enabled)
        self.assertFalse(self.manager.data_root.exists())
        self.assertEqual(self.registry.writes, [])

    def test_enable_installs_stable_copy_and_disable_keeps_files(self):
        status = self.manager.enable()
        self.assertTrue(status.enabled)
        self.assertTrue(status.registered)
        self.assertEqual(status.path.read_bytes(), self.exe.read_bytes())
        self.assertEqual(self.registry.values[RUN_KEY, APP_VALUE], f'"{status.path.resolve()}" --autostart')
        self.assertFalse(self.manager.disable().enabled)
        self.assertTrue(status.path.exists())
        self.assertNotIn((RUN_KEY, APP_VALUE), self.registry.values)

    def test_windows_manual_disable_is_never_undone(self):
        self.registry.values[APPROVED_KEY, APP_VALUE] = b"\x03\x00\x00\x00" + bytes(8)
        status = self.manager.enable()
        self.assertTrue(status.disabled_by_windows)
        self.assertFalse(status.enabled)
        self.assertEqual(self.registry.writes, [])
        self.assertFalse(self.manager.data_root.exists())
        self.assertEqual(self.registry.values[APPROVED_KEY, APP_VALUE][0], 3)

    def test_source_run_never_installs_or_registers(self):
        manager = StartupManager(self.exe, self.root / "source-data", self.registry, frozen=False)
        self.assertEqual(manager.enable().error, "source_run")
        self.assertFalse(manager.data_root.exists())
        self.assertEqual(self.registry.writes, [])

    def test_upgrade_replaces_stable_copy_and_running_stable_copy_skips_copy(self):
        first = self.manager.enable()
        self.exe.write_bytes(b"new version")
        second = self.manager.enable()
        self.assertEqual(second.path.read_bytes(), b"new version")
        self.assertTrue(StartupManager(first.path, self.manager.data_root, self.registry, frozen=True).enable().enabled)
        self.assertEqual(list(second.path.parent.glob("*.tmp")), [])

    def _onedir_manager(self):
        internal = self.exe.parent / "_internal"
        internal.mkdir(exist_ok=True)
        (internal / f"python{sys.version_info.major}{sys.version_info.minor}.dll").write_bytes(b"python DLL fixture")
        (internal / "base_library.zip").write_bytes(b"standard library fixture")
        vendor = internal / "assets/vendor/librehardwaremonitor"
        vendor.mkdir(parents=True, exist_ok=True)
        (vendor / "LibreHardwareMonitorLib.dll").write_bytes(b"LHM fixture")
        (vendor / "manifest.json").write_text('{"fixture":true}', encoding="utf-8")
        return StartupManager(self.exe, self.manager.data_root, self.registry,
                              frozen=True, bundle_kind="onedir")

    def test_onedir_copies_complete_internal_before_registering(self):
        manager = self._onedir_manager()
        status = manager.enable()
        self.assertTrue(status.enabled)
        self.assertEqual(status.path.parent.name, "onedir")
        self.assertEqual((status.path.parent / "_internal/assets/vendor/librehardwaremonitor/LibreHardwareMonitorLib.dll").read_bytes(), b"LHM fixture")
        self.assertTrue((status.path.parent / "startup-bundle.json").is_file())
        self.assertEqual(list(status.path.parent.rglob("*.tmp")), [])

    def test_onedir_missing_copied_dependency_is_reported_and_repairable(self):
        manager = self._onedir_manager()
        status = manager.enable()
        dependency = status.path.parent / "_internal/assets/vendor/librehardwaremonitor/LibreHardwareMonitorLib.dll"
        dependency.unlink()
        broken = manager.status()
        self.assertTrue(broken.registered)
        self.assertFalse(broken.enabled)
        self.assertIn("missing", broken.error)
        self.assertTrue(manager.enable().enabled)
        self.assertEqual(dependency.read_bytes(), b"LHM fixture")

    def test_onedir_source_without_python_dependencies_is_not_registered(self):
        manager = StartupManager(self.exe, self.manager.data_root, self.registry,
                                 frozen=True, bundle_kind="onedir")
        status = manager.enable()
        self.assertFalse(status.enabled)
        self.assertIn("missing", status.error)
        self.assertEqual(self.registry.writes, [])
        self.assertFalse(manager.stable_path.exists())

    def test_running_stable_onedir_requires_its_inventory_and_dependencies(self):
        manager = self._onedir_manager()
        status = manager.enable()
        stable = StartupManager(status.path, manager.data_root, self.registry,
                                frozen=True, bundle_kind="onedir")
        self.assertTrue(stable.enable().enabled)
        (status.path.parent / "startup-bundle.json").unlink()
        result = stable.enable()
        self.assertFalse(result.enabled)
        self.assertIn("inventory", result.error)

    def test_onefile_upgrade_uses_separate_layout_and_preserves_old_files(self):
        onedir = self._onedir_manager()
        old_status = onedir.enable()
        old_dependency = old_status.path.parent / "_internal/assets/vendor/librehardwaremonitor/LibreHardwareMonitorLib.dll"
        legacy = self.manager.data_root / "app/_internal/unknown-user-file.txt"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("preserve", encoding="utf-8")
        self.exe.write_bytes(b"new single-file fixture")
        # Onefile's _MEIPASS is a temporary extraction directory, even when
        # its download directory happens to contain an older _internal.
        with patch("pet_startup.sys._MEIPASS", str(self.root / "_MEI_fixture"), create=True):
            onefile = StartupManager(self.exe, self.manager.data_root, self.registry, frozen=True)
        self.assertEqual(onefile.bundle_kind, "onefile")
        status = onefile.enable()
        self.assertTrue(status.enabled)
        self.assertEqual(status.path.parent.name, "onefile")
        self.assertNotEqual(status.path.parent, old_status.path.parent)
        self.assertFalse((status.path.parent / "_internal").exists())
        self.assertEqual(old_dependency.read_bytes(), b"LHM fixture")
        self.assertEqual(legacy.read_text(encoding="utf-8"), "preserve")

    def test_onedir_detection_uses_actual_pyinstaller_resource_root(self):
        self._onedir_manager()
        with patch("pet_startup.sys._MEIPASS", str(self.exe.parent / "_internal"), create=True):
            manager = StartupManager(self.exe, self.manager.data_root, self.registry, frozen=True)
        self.assertEqual(manager.bundle_kind, "onedir")

    def test_invalid_bundle_inventory_cannot_escape_managed_directory(self):
        manager = self._onedir_manager()
        status = manager.enable()
        inventory = status.path.parent / "startup-bundle.json"
        inventory.write_text(json.dumps({"schema_version": 1, "files": [
            {"path": "../outside.txt", "size": 1}]}), encoding="utf-8")
        result = manager.status()
        self.assertFalse(result.enabled)
        self.assertIn("invalid", result.error)

    def test_startup_destination_escape_is_rejected_without_registry_write(self):
        self.manager.stable_path = self.root / "outside/PawpalCat.exe"
        status = self.manager.enable()
        self.assertFalse(status.enabled)
        self.assertIn("escapes", status.error)
        self.assertEqual(self.registry.writes, [])
        self.assertFalse(self.manager.stable_path.exists())

    def test_copy_failure_never_registers_a_partial_onedir_bundle(self):
        manager = self._onedir_manager()
        with patch("pet_startup.shutil.copy2", side_effect=OSError("disk full fixture")):
            status = manager.enable()
        self.assertFalse(status.enabled)
        self.assertIn("disk full", status.error)
        self.assertEqual(self.registry.writes, [])
        self.assertFalse(manager.stable_path.exists())
        self.assertEqual(list(manager.data_root.rglob("*.tmp")), [])

    def test_mutex_only_acquires_when_explicitly_called(self):
        api = Mock()
        api.CreateMutexW.return_value = 123
        lock = SingleInstance("Local\\PawpalCat-test", api=api)
        api.CreateMutexW.assert_not_called()
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquire())
        api.CreateMutexW.assert_called_once()
        lock.release()
        lock.release()
        api.CloseHandle.assert_called_once_with(123)

    def test_duplicate_instance_handle_is_closed(self):
        api = Mock()

        def duplicate(*_args):
            ctypes.set_last_error(183)
            return 456

        api.CreateMutexW.side_effect = duplicate
        lock = SingleInstance("Local\\PawpalCat-test", api=api)
        self.assertFalse(lock.acquire())
        api.CloseHandle.assert_called_once_with(456)
        lock.release()
        self.assertEqual(api.CloseHandle.call_count, 1)


if __name__ == "__main__":
    unittest.main()
