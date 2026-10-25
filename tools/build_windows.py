"""Build a local portable Windows app; no GitHub or upload operations."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import struct
import ssl
import subprocess
import sys

from prepare_lhm import verify_vendor


VIDEOIO_DLL_SHA256 = "7aabff029d1fa47cf56ede4dbbe05dc62da3208f07cd16471d4c966a33f9aa31"


def verify_videoio_vendor(vendor_dir: Path) -> None:
    required = (
        vendor_dir / "opencv_videoio_ffmpeg500_64.dll",
        vendor_dir / "LICENSE.txt",
        vendor_dir / "LICENSE-3RD-PARTY.txt",
        vendor_dir / "README.md",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing OpenCV Video I/O vendor files: {', '.join(missing)}")
    actual = hashlib.sha256(required[0].read_bytes()).hexdigest()
    if actual != VIDEOIO_DLL_SHA256:
        raise SystemExit(
            "Unexpected OpenCV Video I/O DLL. "
            f"Expected SHA-256 {VIDEOIO_DLL_SHA256}; found {actual}."
        )


def verify_cat_video_assets(asset_dir: Path) -> None:
    manifest_path = asset_dir / "clips.json"
    if not manifest_path.is_file():
        raise SystemExit("Missing cat assets: assets/cat/clips.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid cat animation manifest: {exc}") from exc
    clips = manifest.get("clips")
    expected = {"idle", "sleep", "click", "eat", "groom"}
    if manifest.get("asset_format") != "mp4-color-alpha" or not isinstance(clips, dict):
        raise SystemExit("Cat assets must use the packed color-and-alpha MP4 format.")
    if set(clips) != expected:
        raise SystemExit("Cat assets must contain exactly idle, sleep, click, eat and groom.")
    for state in sorted(expected):
        filename = clips[state].get("file") if isinstance(clips[state], dict) else None
        if (not isinstance(filename, str) or Path(filename).name != filename
                or Path(filename).suffix.lower() != ".mp4"
                or not (asset_dir / filename).is_file()):
            raise SystemExit(f"Missing or invalid MP4 for cat action: {state}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="PawpalCat-v6.3")
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
    verify_videoio_vendor(root / "assets/vendor/opencv-videoio")
    verify_cat_video_assets(root / "assets/cat")
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
