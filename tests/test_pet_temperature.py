from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pet_temperature import DriverAccess, LhmTemperatureReader, TemperatureError, probe_pawnio


class FakeSensor:
    def __init__(self, name, value, identifier, kind="Temperature"):
        self.Name, self.Value = name, value
        self.Identifier, self.SensorType = identifier, kind


class FakeHardware:
    def __init__(self, identifier, name="Hardware", kind="Cpu", sensors=(), children=()):
        self.Identifier, self.Name, self.HardwareType = identifier, name, kind
        self.Sensors, self.SubHardware = list(sensors), list(children)
        self.updates = 0
        self.error = None

    def Update(self):
        self.updates += 1
        if self.error:
            raise self.error


class FakeComputer:
    def __init__(self, hardware=()):
        self.Hardware = list(hardware)
        self.IsMotherboardEnabled = self.IsMemoryEnabled = self.IsControllerEnabled = True
        self.IsNetworkEnabled = self.IsStorageEnabled = self.IsPsuEnabled = self.IsBatteryEnabled = True
        self.open_calls, self.close_calls = [], []
        self.open_error = None

    def Open(self):
        self.open_calls.append(threading.get_ident())
        if self.open_error:
            raise self.open_error

    def Close(self):
        self.close_calls.append(threading.get_ident())


def cpu(value=55):
    return FakeHardware("/intelcpu/0", "Processor", "Cpu", [
        FakeSensor("CPU Package", value, "/intelcpu/0/temperature/0")])


def gpu(value=48, identifier="/gpu-nvidia/0", kind="GpuNvidia"):
    return FakeHardware(identifier, "Graphics", kind, [
        FakeSensor("GPU Core", value, identifier + "/temperature/0")])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.logger = MagicMock()
        self.logger_patch = patch("pet_temperature._logger", return_value=self.logger)
        self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)
        self.readers = []

    def tearDown(self):
        for reader in self.readers:
            if reader._owner in (None, threading.get_ident()):
                reader.close()

    def reader(self, computer=None, driver=None, factory=None):
        computer = computer or FakeComputer([cpu(), gpu()])
        factory = factory or MagicMock(return_value=computer)
        probe = MagicMock(return_value=driver or DriverAccess(True, "ok"))
        reader = LhmTemperatureReader(computer_factory=factory, driver_probe=probe)
        self.readers.append(reader)
        return reader, computer, factory, probe

    def test_initializes_once_and_only_enables_cpu_gpu(self):
        reader, computer, factory, probe = self.reader()
        first, second = reader.read(), reader.read()
        self.assertEqual(first.status, "ok")
        self.assertEqual(second.temperatures.cpus[0].temperature, 55)
        self.assertEqual(len(computer.open_calls), 1)
        factory.assert_called_once_with()
        probe.assert_called_once_with()
        self.assertTrue(computer.IsCpuEnabled)
        self.assertTrue(computer.IsGpuEnabled)
        for name in ("IsMotherboardEnabled", "IsMemoryEnabled", "IsControllerEnabled",
                     "IsNetworkEnabled", "IsStorageEnabled", "IsPsuEnabled", "IsBatteryEnabled"):
            self.assertFalse(getattr(computer, name), name)
        reader.close()
        reader.close()
        self.assertEqual(computer.close_calls, [threading.get_ident()])

    def test_close_failure_is_reported_and_does_not_claim_success(self):
        reader, computer, _, _ = self.reader()
        reader.read()
        computer.Close = MagicMock(side_effect=OSError("device close failed"))
        with self.assertRaises(TemperatureError) as error:
            reader.close()
        self.assertEqual(error.exception.code, "cleanup_failed")
        self.assertIn("device close failed", error.exception.detail)
        reader.close()  # Repeated teardown is safe after reporting the failure.
        computer.Close.assert_called_once()

    def test_subhardware_updates_recursively_and_preserves_sensor_parent(self):
        leaf = FakeHardware("/gpu-nvidia/0/child/leaf", kind="GpuNvidia", sensors=[
            FakeSensor("GPU Core", 61, "/gpu-nvidia/0/child/leaf/temperature/0")])
        child = FakeHardware("/gpu-nvidia/0/child", kind="GpuNvidia", children=[leaf])
        parent = FakeHardware("/gpu-nvidia/0", kind="GpuNvidia", children=[child])
        reader, _, _, _ = self.reader(FakeComputer([parent]))
        result = reader.read()
        self.assertEqual([parent.updates, child.updates, leaf.updates], [1, 1, 1])
        selected = result.temperatures.gpus[0]
        self.assertEqual(selected.temperature, 61)
        self.assertEqual(selected.sensors[0].hardware_id, leaf.Identifier)

    def test_subhardware_cycle_does_not_loop(self):
        parent = gpu()
        child = FakeHardware(parent.Identifier + "/child", kind="GpuNvidia", children=[parent])
        parent.SubHardware.append(child)
        reader, _, _, _ = self.reader(FakeComputer([parent]))
        self.assertEqual(reader.read().temperatures.gpus[0].temperature, 48)
        self.assertEqual((parent.updates, child.updates), (1, 1))

    def test_failed_hardware_update_drops_old_value_but_keeps_other_gpu(self):
        processor, graphics = cpu(), gpu()
        reader, _, _, _ = self.reader(FakeComputer([processor, graphics]))
        self.assertEqual(reader.read().temperatures.cpus[0].temperature, 55)
        processor.error = OSError("CPU went away")
        result = reader.read()
        self.assertIsNone(result.temperatures.cpus[0].temperature)
        self.assertEqual(result.temperatures.gpus[0].temperature, 48)
        self.assertEqual(result.status, "partial")
        self.assertIn("CPU went away", result.error)
        self.assertIn("update failed", json.loads(result.diagnostic_json)["update_errors"][0])

    def test_failed_child_does_not_publish_its_old_sensor_or_hide_parent(self):
        parent = gpu(48)
        child = FakeHardware(parent.Identifier + "/child", kind="GpuNvidia", sensors=[
            FakeSensor("GPU Memory", 80, parent.Identifier + "/child/temperature/0")])
        parent.SubHardware.append(child)
        reader, _, _, _ = self.reader(FakeComputer([parent]))
        reader.read()
        child.error = RuntimeError("unavailable")
        result = reader.read()
        self.assertEqual(result.temperatures.gpus[0].temperature, 48)
        self.assertEqual(len(result.temperatures.gpus[0].sensors), 1)

    def test_sensor_enumeration_failure_does_not_hide_independent_devices(self):
        class BrokenHardware:
            Identifier, Name, HardwareType = "/intelcpu/0", "Processor", "Cpu"
            SubHardware = ()
            def Update(self):
                pass
            @property
            def Sensors(self):
                raise OSError("sensor enumeration failed")
        reader, _, _, _ = self.reader(FakeComputer([gpu(48), BrokenHardware(),
            gpu(61, "/gpu-nvidia/1")]))
        result = reader.read()
        self.assertEqual({item.identifier: item.temperature for item in result.temperatures.gpus},
                         {"/gpu-nvidia/0": 48, "/gpu-nvidia/1": 61})
        self.assertIn("sensor enumeration failed", result.error)

    def test_broken_sensor_value_does_not_discard_other_temperature_sensors(self):
        class BrokenSensor:
            Identifier, Name, SensorType = "/gpu-nvidia/0/temperature/1", "GPU Memory", "Temperature"
            @property
            def Value(self):
                raise OSError("sensor value unavailable")
        graphics = gpu(48)
        graphics.Sensors.append(BrokenSensor())
        reader, _, _, _ = self.reader(FakeComputer([graphics]))
        result = reader.read()
        self.assertEqual(result.temperatures.gpus[0].temperature, 48)

    def test_no_driver_suppresses_cpu_raw_zero_but_preserves_independent_gpu(self):
        reader, _, _, _ = self.reader(FakeComputer([cpu(0), gpu(48)]), DriverAccess(False, "driver_missing", 2))
        result = reader.read()
        self.assertIsNone(result.temperatures.cpus[0].temperature)
        self.assertEqual(result.temperatures.cpus[0].reason, "driver_missing")
        self.assertEqual(result.temperatures.gpus[0].temperature, 48)
        raw = json.loads(result.diagnostic_json)
        self.assertEqual(raw["devices"][0]["sensors"][0]["value"], 0.0)
        self.assertFalse(raw["pawnio"]["available"])
        self.assertEqual(result.status, "partial")

    def test_permission_denied_does_not_trust_even_positive_cpu_value(self):
        reader, _, _, _ = self.reader(FakeComputer([cpu(42)]), DriverAccess(False, "permission_denied", 5))
        result = reader.read()
        self.assertEqual(result.status, "permission_denied")
        self.assertIsNone(result.temperatures.cpus[0].temperature)
        self.assertEqual(json.loads(result.diagnostic_json)["devices"][0]["sensors"][0]["value"], 42)

    def test_null_invalid_values_and_gpu_identity_are_preserved(self):
        reader, _, _, _ = self.reader(FakeComputer([cpu(None), gpu(math.nan),
            gpu(63, "/gpu-nvidia/1"), gpu(37, "/gpu-intel/0", "GpuIntel")]))
        result = reader.read()
        by_id = {item.identifier: item.temperature for item in result.temperatures.gpus}
        self.assertEqual(by_id, {"/gpu-nvidia/0": None, "/gpu-nvidia/1": 63, "/gpu-intel/0": 37})
        self.assertIsNone(result.temperatures.cpus[0].temperature)
        diagnostic = json.loads(result.diagnostic_json, parse_constant=lambda value: self.fail(value))
        self.assertIsNone(diagnostic["devices"][1]["sensors"][0]["value"])

    def test_non_temperature_and_non_cpu_gpu_devices_are_ignored(self):
        graphics = gpu()
        graphics.Sensors.append(FakeSensor("GPU Core", 900, "clock", "Clock"))
        board = FakeHardware("/board", kind="Motherboard", sensors=[FakeSensor("CPU Package", 90, "board-temp")])
        reader, _, _, _ = self.reader(FakeComputer([graphics, board]))
        result = reader.read()
        self.assertFalse(result.temperatures.cpus)
        self.assertEqual(len(result.temperatures.gpus[0].sensors), 1)
        self.assertEqual(board.updates, 0)

    def test_open_failure_is_caught_logged_and_closed_without_reinitializing(self):
        computer = FakeComputer()
        computer.open_error = RuntimeError("Open failed")
        reader, _, factory, _ = self.reader(computer)
        first, second = reader.read(), reader.read()
        self.assertEqual((first.status, second.status), ("initialization_failed", "initialization_failed"))
        self.assertIn("Open failed", first.error)
        self.assertFalse(first.temperatures.cpus)
        factory.assert_called_once()
        reader.close()
        self.assertEqual(len(computer.close_calls), 1)

    def test_typed_runtime_error_survives_as_diagnostic_code(self):
        factory = MagicMock(side_effect=TemperatureError("runtime_incompatible", "CoreCLR already loaded"))
        reader, _, _, _ = self.reader(factory=factory)
        result = reader.read()
        self.assertEqual(result.status, "runtime_incompatible")
        self.assertIn("CoreCLR", result.error)
        self.assertEqual(json.loads(result.diagnostic_json)["status"], "runtime_incompatible")
        self.logger.error.assert_called()

    def test_read_and_close_use_one_worker_and_wrong_thread_access_fails(self):
        reader, computer, _, _ = self.reader()
        failures, results = [], []
        ready, finish = threading.Event(), threading.Event()
        def work():
            try:
                results.append(reader.read())
                ready.set()
                finish.wait(2)
                reader.close()
            except Exception as exc:
                failures.append(exc)
                ready.set()
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(ready.wait(2))
        try:
            with self.assertRaisesRegex(RuntimeError, "owning worker"):
                reader.read()
            with self.assertRaisesRegex(RuntimeError, "owning worker"):
                reader.close()
        finally:
            finish.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(computer.open_calls, computer.close_calls)
        self.assertNotEqual(computer.open_calls, [threading.get_ident()])


class RuntimeLoadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.asset_dir = Path(self.directory.name) / "librehardwaremonitor"
        self.asset_dir.mkdir()
        self.dll = self.asset_dir / "LibreHardwareMonitorLib.dll"
        self.dll.write_bytes(b"test-only-not-a-CLR-library")
        self.manifest = {"library_version": "0.9.6", "runtime": "netfx", "target_framework": "net472",
                         "architecture": "x64", "files": [{"path": self.dll.name,
                         "sha256": hashlib.sha256(self.dll.read_bytes()).hexdigest()}]}
        self.write_manifest()
        self.reader = LhmTemperatureReader(self.asset_dir, driver_probe=lambda: DriverAccess(False, "driver_missing", 2))
        self.logger_patch = patch("pet_temperature._logger", return_value=MagicMock())
        self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)
        self.runtime_patch = patch("pet_temperature._netfx_release", return_value=528040)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.addCleanup(self.reader.close)

    def write_manifest(self):
        (self.asset_dir / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_missing_dll_is_reported_without_importing_pythonnet(self):
        self.dll.unlink()
        result = self.reader.read()
        self.assertEqual(result.status, "dll_missing")
        self.assertIn("LibreHardwareMonitorLib.dll", result.error)

    def test_missing_manifest_is_reported(self):
        (self.asset_dir / "manifest.json").unlink()
        self.assertEqual(self.reader.read().status, "dependency_missing")

    def test_mismatched_runtime_or_library_manifest_is_rejected(self):
        self.manifest["target_framework"] = "net8.0"
        self.write_manifest()
        self.assertEqual(self.reader.read().status, "dll_version_mismatch")

    def test_missing_dependency_is_reported(self):
        self.manifest["files"].append({"path": "dependency.dll", "sha256": "missing"})
        self.write_manifest()
        self.assertEqual(self.reader.read().status, "dependency_missing")

    def test_dependency_hash_mismatch_is_rejected(self):
        self.manifest["files"][0]["sha256"] = "bad"
        self.write_manifest()
        self.assertEqual(self.reader.read().status, "dll_load_failed")

    def test_parent_dependency_path_is_rejected(self):
        self.manifest["files"][0]["path"] = "../outside.dll"
        self.write_manifest()
        self.assertEqual(self.reader.read().status, "dll_load_failed")

    def test_missing_netfx_is_detected_before_loading_dll(self):
        with patch("pet_temperature._netfx_release", return_value=0):
            result = self.reader.read()
        self.assertEqual(result.status, "runtime_missing")

    def test_wrong_architecture_is_detected_before_loading_dll(self):
        with patch("pet_temperature.struct.calcsize", return_value=4):
            result = self.reader.read()
        self.assertEqual(result.status, "architecture_mismatch")

    def test_preloaded_wrong_runtime_is_rejected_without_reloading(self):
        runtime = types.SimpleNamespace(kind="CoreCLR")
        pythonnet = types.ModuleType("pythonnet")
        pythonnet.get_runtime_info = MagicMock(return_value=runtime)
        pythonnet.load = MagicMock()
        with patch.dict(sys.modules, {"pythonnet": pythonnet}):
            result = self.reader.read()
        self.assertEqual(result.status, "runtime_incompatible")
        pythonnet.load.assert_not_called()

    def test_netfx_and_absolute_dll_reference_load_once_without_real_clr(self):
        pythonnet = types.ModuleType("pythonnet")
        pythonnet.get_runtime_info = MagicMock(side_effect=[None, types.SimpleNamespace(kind="NetFx")])
        pythonnet.load = MagicMock()
        computer = FakeComputer([gpu(48)])
        assembly = MagicMock()
        assembly.GetName.return_value.Version = "0.9.6.0"
        assembly.GetType.return_value = None
        clr = types.ModuleType("clr")
        clr.AddReference = MagicMock(return_value=assembly)
        parent = types.ModuleType("LibreHardwareMonitor")
        hardware = types.ModuleType("LibreHardwareMonitor.Hardware")
        hardware.Computer = MagicMock(return_value=computer)
        modules = {"pythonnet": pythonnet, "clr": clr, "LibreHardwareMonitor": parent,
                   "LibreHardwareMonitor.Hardware": hardware}
        with patch.dict(sys.modules, modules), patch("pet_temperature.os.add_dll_directory") as add_dll, \
                patch("pet_temperature._DLL_HANDLES", []), patch.object(sys, "path", list(sys.path)):
            self.assertEqual(self.reader.read().temperatures.gpus[0].temperature, 48)
            self.reader.read()
        pythonnet.load.assert_called_once_with("netfx")
        clr.AddReference.assert_called_once_with(str(self.dll.resolve()))
        add_dll.assert_called_once_with(str(self.asset_dir))
        hardware.Computer.assert_called_once_with()
        self.assertEqual(len(computer.open_calls), 1)


class PawnIoAccessTests(unittest.TestCase):
    """Mock Win32 calls verify access policy without opening any device."""

    def test_probe_only_opens_existing_device_with_library_compatible_sharing(self):
        kernel = MagicMock()
        kernel.CreateFileW.return_value = 1234
        with patch("pet_temperature.ctypes.WinDLL", return_value=kernel):
            result = probe_pawnio()
        self.assertEqual(result, DriverAccess(True, "ok"))
        args = kernel.CreateFileW.call_args.args
        self.assertEqual(args[0], r"\\?\GLOBALROOT\Device\PawnIO")
        self.assertEqual(args[1], 0xC0000000)  # read/write
        self.assertEqual(args[2], 3)  # FILE_SHARE_READ | FILE_SHARE_WRITE
        self.assertEqual(args[4], 3)  # OPEN_EXISTING, never CREATE_* or OPEN_ALWAYS
        kernel.CloseHandle.assert_called_once_with(1234)

    def test_probe_distinguishes_missing_permission_busy_and_other_failures(self):
        import ctypes
        for code, expected in ((2, "driver_missing"), (3, "driver_missing"),
                               (5, "permission_denied"), (32, "driver_busy"),
                               (33, "driver_busy"), (6, "driver_unavailable")):
            with self.subTest(winerror=code):
                kernel = MagicMock()
                kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
                with patch("pet_temperature.ctypes.WinDLL", return_value=kernel), \
                        patch("pet_temperature.ctypes.get_last_error", return_value=code):
                    result = probe_pawnio()
                self.assertEqual(result, DriverAccess(False, expected, code))
                kernel.CloseHandle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
