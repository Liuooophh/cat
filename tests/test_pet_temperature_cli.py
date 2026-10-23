"""Diagnostic-report contract tests using explicit test doubles, not hardware."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pet_temperature import TemperatureResult
from pet_temperature_cli import run_diagnostics
from pet_temperature_models import TemperatureSnapshot


class TemperatureCliTests(unittest.TestCase):
    def test_cleanup_failure_is_explicit_in_json_and_exit_status(self):
        reader = MagicMock()
        reader.read.return_value = TemperatureResult(TemperatureSnapshot(), "no_sensors", diagnostic_json='{"test_double": true}')
        reader.close.side_effect = OSError("close failure")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            with patch("pet_temperature_cli.LhmTemperatureReader", return_value=reader), patch("pet_temperature_cli.sys.stdout", None):
                code = run_diagnostics(["--temperature-diagnostics", str(path), "--samples", "1"])
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertFalse(report["cleanup_completed"])
        self.assertIn("close failure", report["cleanup_error"])
        self.assertFalse(report["any_temperature_available"])


if __name__ == "__main__":
    unittest.main()
