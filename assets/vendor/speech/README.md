# Speech dependencies

Tencent Cloud ASR/TTS use the official modular Python SDK (Apache-2.0). Sounddevice (MIT) loads its wheel's bundled PortAudio library (MIT); raw streams do not require NumPy. PortAudio's original notice is in PortAudio-LICENSE.txt. Only its standard x64 DLL is bundled (no ASIO). SDK networking uses the app's existing verified TLS and proxy routing. No credentials are distributed.

Sources: https://github.com/TencentCloud/tencentcloud-sdk-python and https://github.com/spatialaudio/python-sounddevice . Exact versions and license files are recorded in dependencies.json.

TencentCloud-LICENSE.txt: https://raw.githubusercontent.com/TencentCloud/tencentcloud-sdk-python/master/LICENSE
PortAudio-LICENSE.txt: https://raw.githubusercontent.com/PortAudio/portaudio/master/LICENSE.txt
