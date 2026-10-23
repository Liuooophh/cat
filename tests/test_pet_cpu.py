from __future__ import annotations

import ctypes
import math
import os
import unittest
from unittest.mock import MagicMock, patch

from pet_cpu import (COUNTER_PATHS, LEGACY_TIME_PATH, PDH_FMT_DOUBLE, PDH_FMT_NOCAP100,
                     TIME_PATH, UTILITY_PATH, CpuMonitor, _PdhBackend, _PdhFormattedValue,
                     _SystemTimesBackend)


def system_times(samples):
    values = iter(samples)
    def read(*pointers):
        current = next(values)
        if isinstance(current, Exception):
            raise current
        if current is None:
            return 0
        for pointer, value in zip(pointers, current):
            pointer._obj.dwLowDateTime = value & 0xFFFFFFFF
            pointer._obj.dwHighDateTime = value >> 32
        return 1
    return _SystemTimesBackend(read)


def fake_pdh(*, absent=(), values=None):
    dll = MagicMock()
    handles = {}
    values = dict(values or {})
    def open_query(source, user_data, pointer):
        pointer._obj.value = 700
        return 0
    def add_counter(query, path, user_data, pointer):
        if path in absent:
            return 0xC0000BB9
        handle = len(handles) + 100
        handles[handle] = path
        pointer._obj.value = handle
        return 0
    def formatted(counter, flags, type_pointer, value_pointer):
        data = values.get(handles[counter.value], (0, 0, 50.0))
        status, cstatus, number = data
        value_pointer._obj.CStatus = cstatus
        value_pointer._obj.doubleValue = number
        return status
    dll.PdhOpenQueryW.side_effect = open_query
    dll.PdhAddEnglishCounterW.side_effect = add_counter
    dll.PdhGetFormattedCounterValue.side_effect = formatted
    dll.PdhCollectQueryData.return_value = 0
    dll.PdhCloseQuery.return_value = 0
    return dll, values


class SystemTimesTests(unittest.TestCase):
    def test_first_sample_is_unavailable_then_kernel_includes_idle_exactly_once(self):
        backend = system_times([(0, 0, 0), (20, 100, 100)])
        self.assertIsNone(backend.sample())
        self.assertEqual(backend.sample(), 90.0)

    def test_large_filetimes_keep_both_32_bit_halves(self):
        base = 3 << 40
        backend = system_times([(base, base * 2, base), (base + 90, base * 2 + 100, base + 100)])
        self.assertIsNone(backend.sample())
        self.assertEqual(backend.sample(), 55.0)

    def test_genuine_idle_system_can_report_zero_after_a_real_interval(self):
        backend = system_times([(10, 100, 20), (110, 200, 20)])
        self.assertIsNone(backend.sample())
        self.assertEqual(backend.sample(), 0.0)

    def test_bad_or_reset_deltas_return_none_instead_of_clamping_to_fake_value(self):
        for second in ((11, 100, 20), (200, 101, 20), (9, 110, 20), (11, 99, 21)):
            with self.subTest(second=second):
                backend = system_times([(10, 100, 20), second])
                backend.sample()
                self.assertIsNone(backend.sample())

    def test_failed_native_call_resets_baseline(self):
        backend = system_times([(10, 100, 20), None, (30, 200, 50), (50, 300, 100)])
        backend.sample()
        with patch("pet_cpu.ctypes.get_last_error", return_value=5):
            with self.assertRaises(OSError):
                backend.sample()
        self.assertIsNone(backend.sample())
        self.assertAlmostEqual(backend.sample(), 86.6666666667)

    def test_more_than_one_processor_group_is_not_misreported_as_overall(self):
        with self.assertRaisesRegex(OSError, "processor groups"):
            _SystemTimesBackend(lambda *args: 1, processor_count=128)

    def test_getsystemtimes_binding_uses_three_filetime_pointers_and_bool(self):
        kernel = MagicMock()
        kernel.GetActiveProcessorCount.return_value = 20
        with patch("pet_cpu.ctypes.WinDLL", return_value=kernel):
            backend = _SystemTimesBackend()
        self.assertEqual(kernel.GetSystemTimes.argtypes, [ctypes.POINTER(ctypes.wintypes.FILETIME)] * 3)
        self.assertIs(kernel.GetSystemTimes.restype, ctypes.wintypes.BOOL)
        kernel.GetActiveProcessorCount.assert_called_once_with(0xFFFF)
        backend.close()


class PdhTests(unittest.TestCase):
    def backend(self, **kwargs):
        dll, values = fake_pdh(**kwargs)
        backend = _PdhBackend(dll)
        self.addCleanup(backend.close)
        return backend, dll, values

    def test_english_total_counter_paths_and_warmup_without_gui_sleep(self):
        backend, dll, _ = self.backend()
        self.assertEqual([call.args[1] for call in dll.PdhAddEnglishCounterW.call_args_list], list(COUNTER_PATHS))
        self.assertTrue(all("(_Total)" in path and "\\Process(" not in path for path in COUNTER_PATHS))
        self.assertIsNone(backend.sample())
        dll.PdhGetFormattedCounterValue.assert_not_called()
        self.assertEqual(backend.sample(), 50.0)
        self.assertEqual(backend.counter_path, UTILITY_PATH)
        self.assertEqual(dll.PdhGetFormattedCounterValue.call_args.args[1], PDH_FMT_DOUBLE | PDH_FMT_NOCAP100)

    def test_utility_is_preferred_without_dividing_by_cpu_count(self):
        backend, _, _ = self.backend(values={UTILITY_PATH: (0, 0, 98.0), TIME_PATH: (0, 0, 4.0)})
        backend.sample()
        self.assertEqual(backend.sample(), 98.0)
        self.assertEqual(backend.raw_values[TIME_PATH], 4.0)

    def test_utility_over_100_is_capped_only_in_display_and_retained_raw(self):
        backend, _, _ = self.backend(values={UTILITY_PATH: (0, 0, 135.5)})
        backend.sample()
        self.assertEqual(backend.sample(), 100)
        self.assertEqual(backend.raw_values[UTILITY_PATH], 135.5)

    def test_valid_api_return_with_invalid_cstatus_cannot_supply_fake_value(self):
        backend, _, _ = self.backend(values={UTILITY_PATH: (0, 0xC0000BBA, 98), TIME_PATH: (0, 1, 26)})
        backend.sample()
        self.assertEqual(backend.sample(), 26)
        self.assertEqual(backend.counter_path, TIME_PATH)
        self.assertIn("CStatus", backend.errors[UTILITY_PATH])

    def test_api_format_error_does_not_reuse_previous_good_utility(self):
        backend, _, values = self.backend(values={UTILITY_PATH: (0, 0, 98), TIME_PATH: (0, 0, 26)})
        backend.sample()
        self.assertEqual(backend.sample(), 98)
        values[UTILITY_PATH] = (0xC0000BC6, 0, 98)
        self.assertEqual(backend.sample(), 26)
        self.assertIsNone(backend.raw_values[UTILITY_PATH])

    def test_nonfinite_or_negative_values_are_rejected(self):
        for number in (math.nan, math.inf, -1):
            with self.subTest(number=number):
                backend, _, _ = self.backend(values={path: (0, 0, number) for path in COUNTER_PATHS})
                backend.sample()
                self.assertIsNone(backend.sample())

    def test_missing_utility_uses_available_total_processor_time(self):
        backend, _, _ = self.backend(absent=(UTILITY_PATH, TIME_PATH))
        backend.sample()
        self.assertEqual(backend.sample(), 50)
        self.assertEqual(backend.counter_path, LEGACY_TIME_PATH)

    def test_failed_query_collect_requires_two_fresh_successful_collections(self):
        backend, dll, _ = self.backend()
        backend.sample()
        self.assertEqual(backend.sample(), 50)
        dll.PdhCollectQueryData.side_effect = [0x800007D5, 0, 0]
        with self.assertRaises(OSError):
            backend.sample()
        self.assertIsNone(backend.sample())
        self.assertEqual(backend.sample(), 50)

    def test_no_available_counters_closes_partial_query(self):
        dll, _ = fake_pdh(absent=COUNTER_PATHS)
        with self.assertRaises(OSError):
            _PdhBackend(dll)
        dll.PdhCloseQuery.assert_called_once()

    def test_close_query_is_idempotent_and_prevents_reusing_handles(self):
        backend, dll, _ = self.backend()
        backend.close()
        backend.close()
        self.assertIsNone(backend.sample())
        dll.PdhCloseQuery.assert_called_once()
        dll.PdhCollectQueryData.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Win32 structure layout")
    def test_formatted_counter_structure_has_native_union_alignment(self):
        self.assertEqual(ctypes.sizeof(_PdhFormattedValue), 16)
        self.assertEqual(_PdhFormattedValue.value.offset, 8)


class MonitorTests(unittest.TestCase):
    def make(self, pdh_values, times_values):
        pdh, times = MagicMock(), MagicMock()
        pdh.sample.side_effect = pdh_values
        pdh.counter_path, pdh.errors, pdh.raw_values = UTILITY_PATH, {}, {}
        times.sample.side_effect = times_values
        pdh_factory, times_factory = MagicMock(return_value=pdh), MagicMock(return_value=times)
        monitor = CpuMonitor(pdh_factory=pdh_factory, system_times_factory=times_factory)
        self.addCleanup(monitor.close)
        return monitor, pdh, times, pdh_factory, times_factory

    def test_initialization_is_lazy_then_uses_one_session_and_first_value_none(self):
        monitor, pdh, times, pf, tf = self.make([None, 98], [None, 4])
        pf.assert_not_called()
        tf.assert_not_called()
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.status, "starting")
        self.assertEqual(monitor.sample(), 98)
        self.assertEqual(monitor.source, "pdh_processor_utility")
        self.assertEqual(monitor.counter_path, UTILITY_PATH)
        pf.assert_called_once()
        tf.assert_called_once()
        monitor.close()
        monitor.close()
        pdh.close.assert_called_once()
        times.close.assert_called_once()

    def test_getsystemtimes_is_primed_even_when_utility_is_valid(self):
        monitor, _, times, _, _ = self.make([None, 98, OSError("PDH failed")], [None, 4, 26])
        monitor.sample()
        self.assertEqual(monitor.sample(), 98)
        self.assertEqual(monitor.sample(), 26)
        self.assertEqual(monitor.source, "get_system_times")
        self.assertIn("PDH failed", monitor.last_error)
        self.assertEqual(times.sample.call_count, 3)

    def test_missing_pdh_backend_falls_back_without_repeated_initialization(self):
        times = system_times([(0, 0, 0), (20, 100, 100), (40, 200, 200)])
        factory = MagicMock(side_effect=OSError("PDH missing"))
        monitor = CpuMonitor(pdh_factory=factory, system_times_factory=lambda: times)
        self.addCleanup(monitor.close)
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.sample(), 90)
        self.assertEqual(monitor.sample(), 90)
        factory.assert_called_once()
        self.assertEqual(monitor.source, "get_system_times")
        self.assertIn("PDH missing", monitor.last_error)

    def test_all_backends_fail_with_none_and_explicit_unavailable_status(self):
        monitor, _, _, _, _ = self.make([OSError("counter failed")], [OSError("time failed")])
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.status, "unavailable")
        self.assertIsNone(monitor.source)
        self.assertIn("counter failed", monitor.last_error)
        self.assertIn("time failed", monitor.last_error)

    def test_fresh_sample_recovers_after_failure_without_old_value(self):
        monitor, _, _, _, _ = self.make([98, OSError("failed"), 42], [4, OSError("failed"), 3])
        self.assertEqual(monitor.sample(), 98)
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.sample(), 42)
        self.assertEqual(monitor.last_error, "")

    def test_closed_monitor_does_not_create_or_sample_backends(self):
        monitor, _, _, pf, tf = self.make([50], [5])
        monitor.close()
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.status, "closed")
        pf.assert_not_called()
        tf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
