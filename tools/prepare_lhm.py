"""Prepare pinned official LHM NuGet runtime assemblies for the x64 Windows app.

Build-time only. Does not start LibreHardwareMonitor, initialize hardware, install
drivers or elevate. Existing payloads are verified; nothing is downloaded by the
app or by build_windows.py. The lock file records exact upstream package members.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile


DESTINATION = Path(__file__).resolve().parents[1] / "assets/vendor/librehardwaremonitor"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_checked(path: Path, expected: str) -> bytes:
    if not path.is_file():
        raise RuntimeError(f"Missing temperature dependency: {path}")
    payload = path.read_bytes()
    if _digest(payload) != expected:
        raise RuntimeError(f"Temperature dependency SHA-256 mismatch: {path}")
    return payload


def verify_vendor(folder: Path = DESTINATION) -> dict:
    """Verify all runtime DLLs and required notices, without loading the CLR."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Missing LHM manifest. Run tools/prepare_lhm.py before packaging.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lock = json.loads((folder / "packages.lock.json").read_text(encoding="utf-8"))
    if (manifest.get("library_version"), manifest.get("target_framework"), manifest.get("runtime_identifier")) != ("0.9.6", "net472", "win-x64"):
        raise RuntimeError("Unexpected LHM version, target framework or architecture.")
    runtime_files = manifest.get("files", [])
    expected = {row["file"]: row["file_sha256"] for row in lock["packages"] if row.get("file")}
    if {row["path"]: row["sha256"] for row in runtime_files} != expected:
        raise RuntimeError("LHM manifest does not match the pinned NuGet dependency lock.")
    for row in runtime_files:
        _read_checked(folder / row["path"], row["sha256"])
    for row in lock["notices"]:
        _read_checked(folder / row["path"], row["sha256"])
    for name in ("THIRD-PARTY-NOTICES.txt", "README.md"):
        if not (folder / name).is_file():
            raise RuntimeError(f"Missing temperature redistribution notice: {name}")
    return manifest


def _download(url: str, expected: str) -> bytes:
    if not url.startswith("https://"):
        raise RuntimeError("Dependency URLs must use HTTPS.")
    with urlopen(Request(url, headers={"User-Agent": "PawpalCat-build"}), timeout=45) as response:
        payload = response.read(32 * 1024 * 1024 + 1)
    if len(payload) > 32 * 1024 * 1024 or _digest(payload) != expected:
        raise RuntimeError(f"Upstream SHA-256 mismatch; refusing dependency: {url}")
    return payload


def prepare(folder: Path, cache: Path) -> dict:
    lock = json.loads((folder / "packages.lock.json").read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    packages: dict[str, zipfile.ZipFile] = {}
    for row in lock["packages"]:
        target = cache / f"{row['id'].lower()}.{row['version']}.nupkg"
        if target.is_file():
            payload = _read_checked(target, row["sha256"])
        else:
            payload = _download(row["url"], row["sha256"])
            target.write_bytes(payload)
        package = zipfile.ZipFile(io.BytesIO(payload))
        packages[row["id"]] = package
        if row.get("member"):
            if row["member"].startswith("ref/"):
                raise RuntimeError("Reference-only assemblies cannot be packaged for execution.")
            data = package.read(row["member"])
            if _digest(data) != row["file_sha256"]:
                raise RuntimeError(f"Unexpected runtime assembly in {row['id']}")
            (folder / row["file"]).write_bytes(data)
        license_folder = folder / "licenses"
        license_folder.mkdir(exist_ok=True)
        nuspec = next(name for name in package.namelist() if name.endswith(".nuspec"))
        (license_folder / f"{row['id'].lower()}.nuspec").write_bytes(package.read(nuspec))
        if row["id"] == "HidSharp":
            (license_folder / "hidsharp-LICENSE.txt").write_bytes(package.read("LICENSE.txt"))
    for row in lock["notices"]:
        target = folder / row["path"]
        if target.is_file():
            _read_checked(target, row["sha256"])
            continue
        if "package" in row:
            payload = packages[row["package"]].read(row["member"])
            if _digest(payload) != row["sha256"]:
                raise RuntimeError(f"Unexpected license in {row['package']}")
        else:
            payload = _download(row["url"], row["sha256"])
        target.write_bytes(payload)
    manifest = {
        "schema_version": 1, "library_version": "0.9.6", "runtime": "netfx",
        "target_framework": "net472", "runtime_identifier": "win-x64", "architecture": "x64",
        "minimum_framework_release": 528040,
        "files": [{"path": row["file"], "sha256": row["file_sha256"], "package": row["id"], "version": row["version"]}
                  for row in lock["packages"] if row.get("file")],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return verify_vendor(folder)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Verify local payloads without network access")
    parser.add_argument("--cache-dir", type=Path, default=DESTINATION.parents[2] / "build/vendor-cache")
    args = parser.parse_args()
    manifest = verify_vendor() if args.verify_only else prepare(DESTINATION, args.cache_dir.resolve())
    print(f"LHM {manifest['library_version']} {manifest['runtime_identifier']}/{manifest['target_framework']}: verified {len(manifest['files'])} DLLs")


if __name__ == "__main__":
    main()
