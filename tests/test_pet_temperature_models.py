from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError

from pet_temperature_models import HardwareReading, SensorReading, select_temperatures, temperature_value


def sensor(name: str, value: float | None, identifier: str = "", *, kind: str = "Temperature",
           owner: str = "", owner_type: str = "") -> SensorReading:
    return SensorReading(identifier or name, name, kind, value, owner, "", owner_type)


def device(*sensors: SensorReading, kind: str = "Cpu", identifier: str = "/intelcpu/0",
           name: str = "Processor") -> HardwareReading:
    return HardwareReading(identifier, name, kind, tuple(sensors))


class CpuSelectionTests(unittest.TestCase):
    def test_package_precedes_hotter_core_and_retains_diagnostic_readings(self) -> None:
        result = select_temperatures([device(sensor("CPU Core #1", 84), sensor("CPU Package", 62),
                                             sensor("Core Max", 90), sensor("CPU Fan", 1800, kind="Fan"))])
        cpu = result.cpus[0]
        self.assertEqual((cpu.temperature, cpu.source, cpu.selected_sensor_ids), (62, "cpu_package", ("CPU Package",)))
        self.assertEqual(len(cpu.sensors), 3)
        self.assertFalse(result.gpus)

    def test_amd_real_tdie_precedes_control_offset_and_ccds(self) -> None:
        cpu = select_temperatures([device(sensor("Core (Tctl)", 87), sensor("Core (Tdie)", 67),
                                         sensor("CCD1 (Tdie)", 91), sensor("Core (Tctl/Tdie)", 70),
                                         kind="Cpu", identifier="/amdcpu/0")]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (67, "cpu_tdie"))

    def test_amd_combined_tctl_tdie_is_labelled_as_such(self) -> None:
        cpu = select_temperatures([device(sensor("Core (Tctl/Tdie)", 52))]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (52, "cpu_tctl_tdie"))

    def test_amd_tctl_only_fallback_never_claims_package(self) -> None:
        cpu = select_temperatures([device(sensor("Core (Tctl)", 68))]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (68, "cpu_tctl"))

    def test_core_fallback_uses_maximum_and_records_all_inputs(self) -> None:
        cpu = select_temperatures([device(sensor("CPU Core #1", 51, "core1"),
                                         sensor("CPU Core #2", 58, "core2"),
                                         sensor("CPU Core #3", None, "core3"))]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (58, "cpu_core_max"))
        self.assertEqual(cpu.selected_sensor_ids, ("core1", "core2"))
        self.assertEqual(len(cpu.sensors), 3)

    def test_null_package_falls_back_to_valid_cores(self) -> None:
        cpu = select_temperatures([device(sensor("CPU Package", None), sensor("Core #1", 55))]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (55, "cpu_core_max"))

    def test_ccd_fallback_is_not_mislabelled_as_whole_die(self) -> None:
        cpu = select_temperatures([device(sensor("CCD1 (Tdie)", 60), sensor("CCD2 (Tdie)", 65))]).cpus[0]
        self.assertEqual((cpu.temperature, cpu.source), (65, "cpu_ccd_max"))

    def test_package_offset_and_tjmax_distance_are_not_temperatures(self) -> None:
        cpu = select_temperatures([device(sensor("CPU Package Offset", 5), sensor("Core Distance to TjMax", 52),
                                         sensor("CPU Socket", 40), sensor("CPU VRM", 70),
                                         sensor("CPU SoC", 44))]).cpus[0]
        self.assertIsNone(cpu.temperature)
        self.assertEqual(cpu.reason, "no_cpu_representative_sensor")

    def test_multiple_cpu_packages_do_not_share_core_readings(self) -> None:
        result = select_temperatures([device(sensor("CPU Package", 70), identifier="/intelcpu/1"),
                                      device(sensor("CPU Package", 55), identifier="/intelcpu/0")])
        self.assertEqual([(item.identifier, item.temperature) for item in result.cpus],
                         [("/intelcpu/0", 55), ("/intelcpu/1", 70)])

    def test_intel_distance_to_tjmax_never_becomes_core_temperature_fallback(self) -> None:
        # A distance is measured in Celsius too, but is not a core temperature.
        # Null core readings must not make these valid-looking offsets win.
        cpu = select_temperatures([device(sensor("CPU Core #1", None),
                                         sensor("Core #1 Distance to TjMax", 42),
                                         sensor("CPU Core #2 Distance to TjMax", 45))]).cpus[0]
        self.assertIsNone(cpu.temperature)
        self.assertIsNone(cpu.source)
        self.assertEqual(cpu.selected_sensor_ids, ())
        self.assertEqual(cpu.reason, "no_cpu_representative_sensor")
        self.assertEqual(len(cpu.sensors), 3)


class GpuSelectionTests(unittest.TestCase):
    def test_nvidia_core_precedes_memory_and_hotspot(self) -> None:
        gpu = select_temperatures([device(sensor("GPU Hot Spot", 90), sensor("GPU Memory", 86),
                                         sensor("GPU Core", 62), kind="GpuNvidia", identifier="/gpu-nvidia/0")]).gpus[0]
        self.assertEqual((gpu.temperature, gpu.source), (62, "gpu_core"))

    def test_hotspot_and_memory_only_do_not_supply_fake_core(self) -> None:
        gpu = select_temperatures([device(sensor("GPU Hotspot", 90), sensor("GPU Memory Junction", 86),
                                         kind="GpuAmd", identifier="/gpu-amd/0")]).gpus[0]
        self.assertIsNone(gpu.temperature)
        self.assertEqual(gpu.reason, "no_gpu_core_sensor")

    def test_generic_gpu_label_is_preserved_as_generic_source(self) -> None:
        gpu = select_temperatures([device(sensor("GPU", 48), kind="GpuIntel", identifier="/gpu-intel/0")]).gpus[0]
        self.assertEqual((gpu.temperature, gpu.source), (48, "gpu_temperature"))

    def test_all_gpu_vendors_and_same_name_multi_gpu_stay_independent(self) -> None:
        result = select_temperatures([
            device(sensor("GPU Core", 66), kind="GpuNvidia", identifier="/gpu-nvidia/1", name="Identical GPU"),
            device(sensor("GPU Core", 40), kind="GpuIntel", identifier="/gpu-intel/0", name="Integrated"),
            device(sensor("GPU Core", 60), kind="GpuNvidia", identifier="/gpu-nvidia/0", name="Identical GPU"),
            device(sensor("GPU Core", 58), kind="GpuAmd", identifier="/gpu-amd/0", name="Other GPU"),
        ])
        self.assertEqual([(gpu.identifier, gpu.temperature) for gpu in result.gpus],
                         [("/gpu-amd/0", 58), ("/gpu-intel/0", 40), ("/gpu-nvidia/0", 60), ("/gpu-nvidia/1", 66)])

    def test_invalid_core_does_not_fall_back_to_valid_hotspot(self) -> None:
        gpu = select_temperatures([device(sensor("GPU Core", None), sensor("GPU Hot Spot", 77),
                                         kind="GpuIntel", identifier="/gpu-intel/0")]).gpus[0]
        self.assertIsNone(gpu.temperature)


class IdentityAndValidityTests(unittest.TestCase):
    def test_non_cpu_gpu_hardware_cannot_impersonate_cpu_with_sensor_name(self) -> None:
        result = select_temperatures([device(sensor("CPU Package", 60), kind="SuperIO", identifier="/lpc/nct")])
        self.assertFalse(result.cpus)
        self.assertFalse(result.gpus)

    def test_subhardware_retains_actual_parent_and_belongs_to_root_device(self) -> None:
        child = sensor("GPU Core", 62, "/gpu-nvidia/0/child/temperature/0",
                       owner="/gpu-nvidia/0/child", owner_type="GpuNvidia")
        gpu = select_temperatures([device(child, kind="GpuNvidia", identifier="/gpu-nvidia/0")]).gpus[0]
        self.assertEqual(gpu.temperature, 62)
        self.assertEqual(gpu.sensors[0].hardware_id, "/gpu-nvidia/0/child")

    def test_misgrouped_different_gpu_and_wrong_hardware_kind_are_not_selected(self) -> None:
        gpu = select_temperatures([device(sensor("GPU Core", 90, owner="/gpu-nvidia/1", owner_type="GpuNvidia"),
                                         sensor("GPU Core", 95, owner_type="Cpu"),
                                         sensor("GPU Core", 60, owner="/gpu-nvidia/0", owner_type="GpuNvidia"),
                                         kind="GpuNvidia", identifier="/gpu-nvidia/0")]).gpus[0]
        self.assertEqual(gpu.temperature, 60)
        self.assertEqual(len(gpu.sensors), 3)

    def test_qualified_enum_names_are_supported_but_not_non_temperature_values(self) -> None:
        cpu = select_temperatures([device(sensor("CPU Package", 61, kind="SensorType.Temperature"),
                                         sensor("CPU Package", 123, kind="Load"), kind="HardwareType.Cpu")]).cpus[0]
        self.assertEqual(cpu.temperature, 61)
        self.assertEqual(len(cpu.sensors), 1)

    def test_unavailable_values_never_create_zero_readings(self) -> None:
        for value in (None, math.nan, math.inf, -math.inf, -51, 151, 0, -0.0, True, False, "bad"):
            with self.subTest(value=value):
                cpu = select_temperatures([device(sensor("CPU Package", value))]).cpus[0]
                self.assertIsNone(cpu.temperature)
                self.assertEqual(cpu.reason, "no_valid_temperature_readings")

    def test_zero_sentinel_stays_in_diagnostics_and_nonzero_subzero_is_valid(self) -> None:
        cpu = select_temperatures([device(sensor("CPU Package", 0.0))]).cpus[0]
        self.assertIsNone(cpu.temperature)
        self.assertEqual(cpu.sensors[0].value, 0.0)
        subzero = select_temperatures([device(sensor("CPU Package", -20.0))]).cpus[0]
        self.assertEqual((subzero.temperature, subzero.source), (-20.0, "cpu_package"))

    def test_no_sensors_or_no_devices_produce_unavailable_without_exception(self) -> None:
        self.assertEqual(select_temperatures([]).cpus, ())
        cpu = select_temperatures([device()], sampled_at=100).cpus[0]
        self.assertIsNone(cpu.temperature)
        self.assertEqual(cpu.reason, "no_temperature_sensors")
        self.assertEqual(cpu.sensors, ())

    def test_snapshot_records_are_immutable_and_detach_input_lists(self) -> None:
        source = [sensor("CPU Package", 50)]
        hardware = HardwareReading("/cpu/0", "CPU", "Cpu", source)
        source.clear()
        snapshot = select_temperatures([hardware], sampled_at=100)
        self.assertEqual(snapshot.sampled_at, 100)
        self.assertEqual(snapshot.cpus[0].temperature, 50)
        with self.assertRaises(FrozenInstanceError):
            snapshot.cpus[0].temperature = 90
        with self.assertRaises(FrozenInstanceError):
            snapshot.cpus[0].sensors[0].value = 90

    def test_plausibility_filter_handles_malformed_types(self) -> None:
        self.assertIsNone(temperature_value(object()))
        self.assertIsNone(temperature_value("nan"))
        self.assertEqual(temperature_value(150), 150)
        self.assertEqual(temperature_value(-50), -50)


if __name__ == "__main__":
    unittest.main()
