"""Finite, non-elevating hardware diagnostics for source and frozen releases."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pet_temperature import LhmTemperatureReader, default_log_dir


def run_diagnostics(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temperature-diagnostics", nargs="?", const="", required=True,
                        metavar="REPORT_JSON", help="Write actual hardware results; no GUI or startup registration")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--debug-temperature", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 20 or not 1.0 <= args.interval <= 2.0:
        parser.error("Use 1–20 samples and a 1–2 second interval")
    destination = (Path(args.temperature_diagnostics).resolve() if args.temperature_diagnostics else
                   default_log_dir().parent / "temperature-diagnostics.json")
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    reader = LhmTemperatureReader(root / "assets/vendor", debug=args.debug_temperature)
    report = {"kind": "actual Windows hardware probe", "frozen": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "resource_root": str(root), "pid": os.getpid(),
              "samples": [], "elapsed_seconds": 0.0, "all_temperatures_available": False,
              "any_temperature_available": False, "cleanup_completed": False,
              "no_gui_or_autostart": True}
    started = time.perf_counter()
    exit_code = 0
    try:
        for index in range(args.samples):
            result = reader.read()
            report["samples"].append(json.loads(result.diagnostic_json))
            devices = (*result.temperatures.cpus, *result.temperatures.gpus)
            report["any_temperature_available"] = any(device.temperature is not None for device in devices)
            report["all_temperatures_available"] = bool(devices) and all(device.temperature is not None for device in devices)
            if result.status in {"dll_missing", "dll_load_failed", "dependency_missing", "runtime_missing",
                                 "runtime_incompatible", "architecture_mismatch", "dll_version_mismatch", "initialization_failed"}:
                exit_code = 2
                break
            if index + 1 < args.samples:
                time.sleep(args.interval)
    except Exception as exc:
        report["diagnostic_error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 2
    finally:
        try:
            reader.close()
            report["cleanup_completed"] = True
        except Exception as exc:
            report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 2
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        except OSError as exc:
            if sys.stderr:
                print(f"Cannot write diagnostic report: {exc}", file=sys.stderr)
            return 3
    if sys.stdout:
        print(json.dumps({"report": str(destination), "any_temperature_available": report["any_temperature_available"],
                          "all_temperatures_available": report["all_temperatures_available"], "exit_code": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_diagnostics())
