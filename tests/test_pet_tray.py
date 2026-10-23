"""Tray-only temperature presentation; test data is simulated explicitly."""
from types import SimpleNamespace
import unittest

from pet_tray import temperature_lines, temperature_tooltip, representative_values, temperature_icon


def device(name, value, source=None, reason="selected"):
    return SimpleNamespace(name=name, temperature=value, source=source, reason=reason)


class TrayTests(unittest.TestCase):
    def test_all_gpus_stay_distinct_and_unknown_values_are_not_zero(self):
        snapshot = SimpleNamespace(cpu_temperatures=(device("CPU", None, reason="driver_missing"),),
            gpu_temperatures=(device("Intel GPU", None, reason="no_temperature_sensors"),
                              device("NVIDIA GPU", 69, "gpu_core")))
        lines = temperature_lines(snapshot)
        self.assertEqual(len(lines), 3)
        self.assertIn("CPU: --°C", lines[0])
        self.assertIn("缺少驱动", lines[0])
        self.assertIn("GPU1: --°C", lines[1])
        self.assertIn("GPU2 Core: 69°C", lines[2])
        self.assertEqual(representative_values(snapshot), (69, None))
        self.assertNotIn("0°C", "\n".join(lines))

    def test_representative_maximum_does_not_replace_per_device_menu(self):
        snapshot = SimpleNamespace(cpu_temperatures=(device("CPU", 59, "cpu_tctl"),),
            gpu_temperatures=(device("GPU A", 65, "gpu_core"), device("GPU B", 78, "gpu_core")))
        self.assertEqual(representative_values(snapshot), (78, 59))
        text = "\n".join(temperature_lines(snapshot))
        self.assertIn("CPU Tctl: 59°C", text)
        self.assertIn("GPU1 Core: 65°C", text)
        self.assertIn("GPU2 Core: 78°C", text)

    def test_native_tooltip_fits_utf16_limit_with_many_devices(self):
        snapshot = SimpleNamespace(cpu_temperatures=(), gpu_temperatures=tuple(
            device("GPU💤" * 30, None, reason="unavailable") for _ in range(30)))
        title = temperature_tooltip(snapshot, "ms")
        self.assertLessEqual(len(title.encode("utf-16-le")), 254)
        self.assertEqual(temperature_tooltip(snapshot, "zh-CN", False), "猫咪")
        self.assertNotIn("tray.reason", title)

    def test_numeric_icon_handles_unavailable_without_inventing_readings(self):
        icon = temperature_icon(None, None)
        self.assertEqual(icon.mode, "RGBA")
        self.assertEqual(icon.size, (32, 32))
        self.assertIsNotNone(icon.getbbox())
        self.assertNotEqual(icon.tobytes(), temperature_icon(69, 87).tobytes())


if __name__ == "__main__":
    unittest.main()
