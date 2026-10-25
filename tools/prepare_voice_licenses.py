"""Copy only installed speech dependencies' license notices for distribution."""
from pathlib import Path
import importlib.metadata
import json
from urllib.request import urlopen

PACKAGES = ("sounddevice", "tencentcloud-sdk-python-common", "tencentcloud-sdk-python-asr",
            "tencentcloud-sdk-python-tts", "requests", "urllib3", "certifi", "charset-normalizer", "idna")


def main():
    target = Path(__file__).resolve().parents[1] / "assets/vendor/speech"
    target.mkdir(parents=True, exist_ok=True)
    upstream_licenses = {
        "TencentCloud-LICENSE.txt": "https://raw.githubusercontent.com/TencentCloud/tencentcloud-sdk-python/master/LICENSE",
        "PortAudio-LICENSE.txt": "https://raw.githubusercontent.com/PortAudio/portaudio/master/LICENSE.txt",
    }
    for filename, url in upstream_licenses.items():
        path = target / filename
        if not path.is_file():
            with urlopen(url, timeout=30) as response:
                path.write_bytes(response.read())
    manifest = []
    for name in PACKAGES:
        dist = importlib.metadata.distribution(name)
        copied = []
        for entry in dist.files or ():
            if (Path(str(entry)).name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS"))
                    and ".dist-info/" in str(entry)):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    destination = target / name / Path(str(entry)).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read_bytes())
                    copied.append(str(destination.relative_to(target)))
        if not copied and name.startswith("tencentcloud-sdk-python-"):
            copied.append("TencentCloud-LICENSE.txt")
        if not copied:
            raise RuntimeError(f"License not found: {name}")
        manifest.append({"package": name, "version": dist.version, "notices": copied})
    (target / "dependencies.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (target / "README.md").write_text(
        "# Speech dependencies\n\nTencent Cloud ASR/TTS use the official modular Python SDK (Apache-2.0). "
        "Sounddevice (MIT) loads its wheel's bundled PortAudio library (MIT); raw streams do not require NumPy. "
        "PortAudio's original notice is in PortAudio-LICENSE.txt. Only its standard x64 DLL is bundled (no ASIO). "
        "SDK networking uses the app's existing verified TLS and proxy routing. No credentials are distributed.\n\n"
        "Sources: https://github.com/TencentCloud/tencentcloud-sdk-python and "
        "https://github.com/spatialaudio/python-sounddevice . Exact versions and license files are recorded in dependencies.json.\n\n"
        + "\n".join(f"{name}: {url}" for name, url in upstream_licenses.items()) + "\n",
        encoding="utf-8")
    print(f"Collected license notices for {len(manifest)} packages")


if __name__ == "__main__":
    main()
