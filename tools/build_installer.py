"""Build a per-user setup from the existing onedir app; never install drivers."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging/windows"


def verify_hash(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: {path.name}")


def verify_signature(path: Path, publisher: str) -> dict:
    escaped = str(path.resolve()).replace("'", "''")
    command = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
               f"$s=Get-AuthenticodeSignature -LiteralPath '{escaped}'; "
               "@{status=[string]$s.Status;publisher=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress")
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if shell is None:
        raise ValueError("PowerShell is required to verify installer signatures")
    result = subprocess.run([shell, "-NoProfile", "-Command", command],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    signature = json.loads(result.stdout)
    if signature["status"] != "Valid" or publisher not in signature["publisher"]:
        raise ValueError(f"Invalid signature or publisher: {path.name}")
    return signature


def prepare_prerequisites(destination: Path, cache: Path | None = None) -> list[dict]:
    destination.mkdir(parents=True, exist_ok=True)
    lock = json.loads((SPEC / "prerequisites.lock.json").read_text(encoding="utf-8"))
    verified = []
    for row in lock["files"]:
        target = destination / row["name"]
        if not target.is_file():
            cached = cache / row["cache_name"] if cache is not None else None
            if cached is not None and cached.is_file():
                shutil.copy2(cached, target)
            else:
                with urlopen(Request(row["url"], headers={"User-Agent": "PawpalCat-build"}), timeout=60) as response:
                    target.write_bytes(response.read())
        verify_hash(target, row["sha256"])
        signature = verify_signature(target, row["publisher"])
        verified.append({**row, "signature": signature})
    return verified


def verify_payload(payload: Path) -> None:
    for relative in ("PawpalCat.exe", "_internal/base_library.zip",
                     "_internal/assets/cat/clips.json", "_internal/assets/ui/cream-cat-microphone.png"):
        if not (payload / relative).is_file():
            raise ValueError(f"Incomplete installer payload: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="6.11")
    parser.add_argument("--payload", type=Path, default=ROOT / "build/installer-payload/PawpalCat")
    parser.add_argument("--iscc", type=Path, default=ROOT.parent / "installer-tools/InnoSetup/ISCC.exe")
    parser.add_argument("--prerequisite-cache", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Build setup on Windows.")
    verify_payload(args.payload)
    verified = prepare_prerequisites(ROOT / "build/installer-prerequisites", args.prerequisite_cache)
    if not args.iscc.is_file():
        raise SystemExit("Install Inno Setup 6.7.3 or pass --iscc with its ISCC.exe path.")
    subprocess.run([str(args.iscc.resolve()), f"/DAppVersion={args.version}",
                    f"/DPayloadDir={args.payload.resolve()}",
                    f"/DPrerequisiteDir={(ROOT / 'build/installer-prerequisites').resolve()}",
                    str(SPEC / "pawpal.iss")], check=True)
    output = ROOT / "dist" / f"PawpalCat-v{args.version}-Setup.exe"
    report = {"schema_version": 1, "version": args.version,
              "setup_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "prerequisites": verified, "drivers_installed_during_build": False}
    output.with_suffix(".provenance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Setup ready: {output.name}; prerequisites verified; no driver installed.")


if __name__ == "__main__":
    main()
