from __future__ import annotations

import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

from pet_metrics import MetricsMonitor, _FrameWindow, _target_from_pid
from pet_temperature import TemperatureResult
from pet_temperature_models import DeviceTemperature, TemperatureSnapshot


def row(pid: int = 42, interval: str = "16.6666667", chain: str = "0x1", dropped: str = "0") -> dict[str, str]:
    return {"ProcessID": str(pid), "msBetweenDisplayChange": interval, "SwapChainAddress": chain, "Dropped": dropped}


class DisplayFpsTests(unittest.TestCase):
    def test_uses_displayed_intervals_not_pet_timer_or_present_rate(self) -> None:
        frames = _FrameWindow()
        for index in range(60):
            sample = row()
            sample["msBetweenPresents"] = "4"
            frames.add(sample, 10 + index / 60)
        value, timestamp = frames.value(11, frozenset({42}))
        self.assertAlmostEqual(value, 60, places=3)
        self.assertGreater(timestamp, 10)

    def test_separate_swap_chains_are_never_added(self) -> None:
        frames = _FrameWindow()
        for index in range(60):
            frames.add(row(chain="0x1"), 10 + index / 60)
        for index in range(30):
            frames.add(row(interval="33.3333333", chain="0x2"), 10 + index / 30)
        self.assertAlmostEqual(frames.value(11, frozenset({42}))[0], 60, places=3)

    def test_other_process_does_not_leak_into_target(self) -> None:
        frames = _FrameWindow()
        for index in range(30):
            frames.add(row(pid=500), 10 + index / 60)
        self.assertIsNone(frames.value(11, frozenset({42}))[0])

    def test_dropped_and_invalid_values_are_ignored(self) -> None:
        frames = _FrameWindow()
        for interval in ("NA", "0", "-10", "nan", "inf", "10001"):
            self.assertFalse(frames.add(row(interval=interval), 10))
        self.assertFalse(frames.add(row(dropped="1"), 10))
        self.assertIsNone(frames.value(10, frozenset({42}))[0])

    def test_stale_frames_clear_value(self) -> None:
        frames = _FrameWindow()
        frames.add(row(), 10)
        frames.add(row(), 10.02)
        self.assertIsNotNone(frames.value(10.1, frozenset({42}))[0])
        self.assertEqual(frames.value(12, frozenset({42})), (None, 0.0))

    def test_one_row_is_not_a_stable_measurement(self) -> None:
        frames = _FrameWindow()
        frames.add(row(), 10)
        self.assertIsNone(frames.value(10, frozenset({42}))[0])

    def test_browser_matches_own_child_gpu_only(self) -> None:
        table = {1: (0, "PawpalCat.exe"), 20: (0, "msedge.exe"), 21: (20, "msedge.exe"),
                 22: (21, "msedge.exe"), 23: (20, "unrelated.exe"), 50: (0, "dwm.exe")}
        target, _ = _target_from_pid(20, table, 1)
        self.assertEqual(target.process_ids, frozenset({20, 21, 22}))
        self.assertEqual(target.name, "msedge.exe")

    def test_self_and_dwm_are_not_fps_targets(self) -> None:
        table = {1: (2, "PawpalCat.exe"), 2: (0, "PawpalCat.exe"), 50: (0, "dwm.exe")}
        self.assertEqual(_target_from_pid(1, table, 1), (None, "self_foreground"))
        self.assertEqual(_target_from_pid(2, table, 1), (None, "self_foreground"))
        self.assertEqual(_target_from_pid(50, table, 1), (None, "no_foreground"))


def temperature_result(cpu_value=54.0, gpu_value=48.0):
    processor = DeviceTemperature("/intelcpu/0", "Processor", "Cpu", cpu_value, "cpu_package",
                                  ("/intelcpu/0/temperature/0",), (), "selected")
    graphics = DeviceTemperature("/gpu-nvidia/0", "Graphics", "GpuNvidia", gpu_value, "gpu_core",
                                 ("/gpu-nvidia/0/temperature/0",), (), "selected")
    return TemperatureResult(TemperatureSnapshot((processor,), (graphics,), 1000.0), "ok",
                             runtime="fake netfx", diagnostic_json='{"test": true}')


class TemperatureWorkerTests(unittest.TestCase):
    """Worker integration uses fakes only; no CLR, sensor or driver access."""

    def run_polls(self, monitor, count=2, after_wait=None):
        snapshots = []
        def wait(_seconds):
            snapshots.append(monitor.snapshot())
            if after_wait:
                after_wait(len(snapshots))
            if len(snapshots) >= count:
                monitor._stop.set()
            return monitor._stop.is_set()
        with patch.object(monitor._stop, "wait", side_effect=wait):
            monitor._temperature_worker()
        return snapshots

    def test_reader_is_constructed_once_per_enabled_session_and_closed_in_finally(self):
        reader = MagicMock()
        reader.read.return_value = temperature_result()
        factory = MagicMock(return_value=reader)
        monitor = MetricsMonitor(temperature_reader_factory=factory)
        snapshots = self.run_polls(monitor, 3)
        factory.assert_called_once_with()
        self.assertEqual(reader.read.call_count, 3)
        reader.close.assert_called_once_with()
        self.assertEqual([item.cpu_temperature for item in snapshots], [54, 54, 54])
        self.assertEqual(snapshots[-1].gpu_temperatures[0].temperature, 48)
        self.assertEqual(snapshots[-1].temperature_source, "Processor: cpu_package")
        self.assertEqual(snapshots[-1].temperature_runtime, "fake netfx")
        self.assertEqual(snapshots[-1].temperature_updated_at, 1000.0)

    def test_factory_and_reader_failure_recover_without_crashing_worker(self):
        reader = MagicMock()
        reader.read.side_effect = [OSError("sensor read failed"), temperature_result()]
        factory = MagicMock(side_effect=[RuntimeError("factory failed"), reader])
        monitor = MetricsMonitor(temperature_reader_factory=factory)
        snapshots = self.run_polls(monitor, 3)
        self.assertEqual([item.temperature_status for item in snapshots], ["read_failed", "read_failed", "ok"])
        self.assertIn("factory failed", snapshots[0].temperature_error)
        self.assertIn("sensor read failed", snapshots[1].temperature_error)
        self.assertEqual(snapshots[-1].temperature_error, "")
        self.assertEqual(factory.call_count, 2)
        reader.close.assert_called_once()

    def test_failed_poll_clears_previously_published_values(self):
        reader = MagicMock()
        reader.read.side_effect = [temperature_result(), RuntimeError("disconnected")]
        monitor = MetricsMonitor(temperature_reader_factory=lambda: reader)
        snapshots = self.run_polls(monitor)
        self.assertEqual(snapshots[0].cpu_temperature, 54)
        self.assertIsNone(snapshots[1].cpu_temperature)
        self.assertEqual(snapshots[1].cpu_temperatures, ())
        self.assertEqual(snapshots[1].gpu_temperatures, ())

    def test_disabling_clears_values_closes_reader_and_reenable_starts_fresh(self):
        first, second = MagicMock(), MagicMock()
        first.read.return_value = temperature_result(54, 48)
        second.read.return_value = temperature_result(58, 52)
        factory = MagicMock(side_effect=[first, second])
        monitor = MetricsMonitor(temperature_reader_factory=factory)
        def configure(poll):
            if poll == 1:
                monitor.configure(temperature_enabled=False)
                self.assertIsNone(monitor.snapshot().cpu_temperature)
                self.assertEqual(monitor.snapshot().gpu_temperatures, ())
            elif poll == 2:
                monitor.configure(temperature_enabled=True)
        snapshots = self.run_polls(monitor, 3, configure)
        self.assertEqual([item.temperature_status for item in snapshots], ["ok", "disabled", "ok"])
        self.assertEqual(snapshots[2].cpu_temperature, 58)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_disabled_start_does_not_initialize_library(self):
        factory = MagicMock()
        monitor = MetricsMonitor(temperature_reader_factory=factory)
        monitor.configure(temperature_enabled=False)
        self.run_polls(monitor)
        factory.assert_not_called()
        self.assertEqual(monitor.snapshot().temperature_status, "disabled")

    def test_disable_during_read_does_not_republish_old_result(self):
        monitor = MetricsMonitor()
        reader = MagicMock()
        def read():
            monitor.configure(temperature_enabled=False)
            return temperature_result()
        reader.read.side_effect = read
        monitor._temperature_reader_factory = lambda: reader
        self.run_polls(monitor, 1)
        self.assertEqual(monitor.snapshot().temperature_status, "disabled")
        self.assertIsNone(monitor.snapshot().cpu_temperature)
        self.assertEqual(monitor.snapshot().gpu_temperatures, ())
        reader.close.assert_called_once()

    def test_final_cleanup_failure_is_isolated(self):
        reader = MagicMock()
        reader.read.return_value = temperature_result()
        reader.close.side_effect = RuntimeError("Close failed")
        monitor = MetricsMonitor(temperature_reader_factory=lambda: reader)
        self.run_polls(monitor, 1)
        reader.close.assert_called_once()

    def test_disable_cleanup_failure_does_not_terminate_temperature_worker(self):
        first, second = MagicMock(), MagicMock()
        first.read.return_value = temperature_result(54, 48)
        first.close.side_effect = RuntimeError("Close failed")
        second.read.return_value = temperature_result(58, 52)
        monitor = MetricsMonitor(temperature_reader_factory=MagicMock(side_effect=[first, second]))
        def configure(poll):
            if poll == 1:
                monitor.configure(temperature_enabled=False)
            elif poll == 2:
                monitor.configure(temperature_enabled=True)
        snapshots = self.run_polls(monitor, 3, configure)
        self.assertEqual(snapshots[-1].cpu_temperature, 58)
        second.close.assert_called_once()

    def test_disable_during_failed_read_preserves_disabled_status(self):
        monitor = MetricsMonitor()
        reader = MagicMock()
        def read():
            monitor.configure(temperature_enabled=False)
            raise OSError("disabled while reading")
        reader.read.side_effect = read
        monitor._temperature_reader_factory = lambda: reader
        self.run_polls(monitor, 1)
        self.assertEqual(monitor.snapshot().temperature_status, "disabled")
        self.assertIsNone(monitor.snapshot().cpu_temperature)

    def test_reader_creation_read_and_close_stay_on_background_thread(self):
        threads, errors = [], []
        complete = threading.Event()
        class Reader:
            def read(self):
                threads.append(("read", threading.get_ident()))
                complete.set()
                return temperature_result()
            def close(self):
                threads.append(("close", threading.get_ident()))
        def factory():
            threads.append(("create", threading.get_ident()))
            return Reader()
        monitor = MetricsMonitor(temperature_reader_factory=factory)
        def work():
            try:
                monitor._temperature_worker()
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=work)
        monitor._threads = [worker]
        worker.start()
        self.assertTrue(complete.wait(2))
        monitor.stop()
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual([name for name, _ in threads], ["create", "read", "close"])
        self.assertEqual(len({identifier for _, identifier in threads}), 1)
        self.assertNotEqual(threads[0][1], threading.get_ident())


class MonitorLifecycleTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_disable_removes_values(self) -> None:
        monitor = MetricsMonitor()
        monitor._publish(fps=60.0, cpu_temperature=50.0)
        with self.assertRaises(FrozenInstanceError):
            monitor.snapshot().fps = 99
        monitor.configure(fps_enabled=False, temperature_enabled=False)
        self.assertIsNone(monitor.snapshot().fps)
        self.assertEqual(monitor.snapshot().fps_status, "disabled")
        self.assertIsNone(monitor.snapshot().cpu_temperature)

    def test_shutdown_only_terminates_own_session(self) -> None:
        monitor = MetricsMonitor()
        process = MagicMock()
        process.poll.return_value = None
        monitor._process = process
        with patch("pet_metrics.subprocess.run") as run:
            monitor._stop_capture()
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-2:], [monitor._session, "--terminate_existing_session"])
        self.assertNotIn("--restart_as_admin", arguments)
        process.wait.assert_called()
        self.assertIsNone(monitor._process)

    def test_capture_does_not_elevate_or_write_csv_files(self) -> None:
        monitor = MetricsMonitor()
        with patch.object(Path, "is_file", return_value=True), patch("pet_metrics.subprocess.Popen") as popen, patch("pet_metrics.threading.Thread"):
            monitor._start_capture()
        arguments = popen.call_args.args[0]
        self.assertTrue(arguments[0].endswith("PawpalMetricsGuard.exe"))
        self.assertTrue(arguments[2].endswith("PresentMon-2.6.0-x64.exe"))
        self.assertNotIn("--restart_as_admin", arguments)
        self.assertNotIn("--output_file", arguments)
        self.assertNotIn("--stop_existing_session", arguments)


if __name__ == "__main__":
    unittest.main()
