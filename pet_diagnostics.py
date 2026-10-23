"""Finite system/network checks without GUI, saved credentials or AI generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback
from urllib.error import HTTPError
from urllib.request import Request

from pet_context import CpuMonitor, search_weather_locations, fetch_weather_at
from pet_network import build_network_opener, network_failure
from pet_temperature import default_log_dir


def run_diagnostics(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-diagnostics", nargs="?", const="", required=True, metavar="REPORT_JSON")
    args = parser.parse_args(argv)
    path = Path(args.system_diagnostics).resolve() if args.system_diagnostics else default_log_dir().parent / "system-diagnostics.json"
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "actual_windows_cpu": [], "weather": {}, "deepseek_network": {},
              "credentials_read": False, "ai_generation_requests": 0, "gui_or_autostart": False}
    monitor = CpuMonitor()
    try:
        for index in range(3):
            value = monitor.sample()
            report["actual_windows_cpu"].append({"value": value, **monitor.diagnostics})
            if index < 2:
                time.sleep(1)
    except Exception as exc:
        report["cpu_error"] = network_failure(exc)[1]
    finally:
        monitor.close()
        report["cpu_cleanup_status"] = monitor.status
    try:
        # A public test location; never reads a user's saved city or coordinates.
        matches = search_weather_locations("Kuala Lumpur", "en")
        if not matches:
            raise ValueError("No public test city returned")
        reading = fetch_weather_at(matches[0], "en")
        report["weather"] = {"status": "ok", "city": reading.city, "temperature_c": reading.temperature,
            "observed_at": reading.observed_at, "source": "Open-Meteo", "test_location_not_user_location": True}
    except Exception as exc:
        report["weather"] = {"status": "error", "detail": network_failure(exc)[1],
                             "diagnostic_trace": traceback.format_exc(limit=12)}
    request = Request("https://api.deepseek.com/models", headers={"Accept": "application/json", "User-Agent": "PawpalCat/5"})
    try:
        with build_network_opener(url=request.full_url).open(request, timeout=15) as response:
            report["deepseek_network"] = {"reachable": True, "http_status": response.status, "authenticated": False}
    except HTTPError as exc:
        report["deepseek_network"] = {"reachable": True, "http_status": exc.code, "authenticated": False,
            "note": "401 is expected here: no API key was sent. This is not a chat authentication test."}
    except Exception as exc:
        report["deepseek_network"] = {"reachable": False, "detail": network_failure(exc)[1],
                                      "diagnostic_trace": traceback.format_exc(limit=12)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    except OSError:
        return 3
    if sys.stdout:
        print(json.dumps({"report": str(path), "weather": report["weather"]["status"],
                          "deepseek_reachable": report["deepseek_network"]["reachable"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_diagnostics())
