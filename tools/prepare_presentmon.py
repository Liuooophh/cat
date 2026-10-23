"""Fetch the pinned official standalone PresentMon binary and its MIT license.

Run once before packaging; this does not install a service or driver. The app
does not download executable code at runtime.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen


VERSION = "2.6.0"
SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
DOWNLOAD_URL = f"https://github.com/GameTechDev/PresentMon/releases/download/v{VERSION}/PresentMon-{VERSION}-x64.exe"
LICENSE_URL = f"https://raw.githubusercontent.com/GameTechDev/PresentMon/v{VERSION}/LICENSE.txt"
DESTINATION = Path(__file__).resolve().parents[1] / "assets" / "vendor" / "presentmon"


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    target = DESTINATION / f"PresentMon-{VERSION}-x64.exe"
    if target.exists():
        payload = target.read_bytes()
    else:
        with urlopen(Request(DOWNLOAD_URL, headers={"User-Agent": "PawpalCat-build"}), timeout=30) as response:
            payload = response.read(4_194_304)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != SHA256:
        raise SystemExit(f"PresentMon SHA-256 mismatch: {actual}. Refusing to package this file.")
    if not target.exists():
        target.write_bytes(payload)
    license_path = DESTINATION / "LICENSE.txt"
    if not license_path.exists():
        with urlopen(Request(LICENSE_URL, headers={"User-Agent": "PawpalCat-build"}), timeout=30) as response:
            license_path.write_bytes(response.read(65_536))
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    guardian = DESTINATION / "PawpalMetricsGuard.exe"
    source = Path(__file__).resolve().with_name("metrics_guard.cs")
    if not guardian.exists() or source.stat().st_mtime > guardian.stat().st_mtime:
        if not compiler.is_file():
            raise SystemExit("The Windows .NET Framework C# compiler is required to build the metrics guardian.")
        subprocess.run([str(compiler), "/nologo", "/optimize+", "/target:exe", "/platform:anycpu", f"/out:{guardian}", str(source)], check=True)
    print(f"PresentMon {VERSION}: verified {actual}")


if __name__ == "__main__":
    main()
