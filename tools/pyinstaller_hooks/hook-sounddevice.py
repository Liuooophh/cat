"""Bundle only the standard x64 PortAudio used by this Windows application."""
from pathlib import Path
from PyInstaller.utils.hooks import get_module_file_attribute

root = Path(get_module_file_attribute("sounddevice")).parent
source = root / "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll"
if not source.is_file():
    raise RuntimeError("sounddevice's standard x64 PortAudio DLL is missing")
binaries = [(str(source), "_sounddevice_data/portaudio-binaries")]
hiddenimports = ["_sounddevice", "_sounddevice_data"]
