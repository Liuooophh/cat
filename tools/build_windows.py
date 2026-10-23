"""Build a local portable Windows app; no GitHub or upload operations."""
from pathlib import Path
import argparse
import importlib.metadata
import struct
import ssl
import subprocess
import sys

from prepare_lhm import verify_vendor


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="PawpalCat-v5")
    parser.add_argument("--output-dir", type=Path, default=root / "dist")
    layout = parser.add_mutually_exclusive_group()
    layout.add_argument("--onedir", action="store_true", help="Build a portable directory (default)")
    layout.add_argument("--onefile", action="store_true", help="Build a single EXE; also test its extraction path")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Build the Windows app on Windows.")
    if struct.calcsize("P") != 8:
        raise SystemExit("The bundled LibreHardwareMonitor implementation requires x64 Python.")
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise SystemExit("Use CPython 3.10-3.13 x64; the tested build uses CPython 3.12.14.")
    for package, expected in (("pythonnet", "3.0.5"), ("clr-loader", "0.2.7.post0")):
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SystemExit("Install requirements.txt before building temperature support.") from exc
        if actual != expected:
            raise SystemExit(f"Expected {package} {expected}; found {actual}.")
    verify_vendor(root / "assets/vendor/librehardwaremonitor")
    if not (root / "assets/cat/clips.json").is_file():
        raise SystemExit("Missing cat assets: assets/cat/clips.json")
    runtime_resources = []
    # This managed build runtime patches SSLContext to load a local trust-policy
    # resource. Preserve that policy in frozen builds; stock Python needs none.
    ssl_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if ssl_policy.is_file():
        runtime_resources.extend(["--add-data", f"{ssl_policy};."])
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--onefile" if args.onefile else "--onedir", "--name", args.name,
        "--icon", str(root / "pet_icon.ico"),
        "--add-data", f"{root / 'assets/cat'};assets/cat",
        "--add-data", f"{root / 'assets/vendor'};assets/vendor",
        "--add-data", f"{root / 'pet_icon.png'};.",
        "--add-data", f"{root / 'pet_icon.ico'};.",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "clr",
        "--hidden-import", "_cffi_backend",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--copy-metadata", "pythonnet",
        "--copy-metadata", "clr-loader",
        "--copy-metadata", "cffi",
        *runtime_resources,
        "--exclude-module", "numpy", "--exclude-module", "matplotlib",
        "--distpath", str(args.output_dir.resolve()),
        "--workpath", str(root / "build" / args.name),
        "--specpath", str(root / "build" / args.name),
        str(root / "desktop_ai_pet.pyw"),
    ], cwd=root, check=True)


if __name__ == "__main__":
    main()
