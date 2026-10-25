"""On-demand Tencent ASR/TTS. Imports never record, connect or play audio."""
from __future__ import annotations

import array
import base64
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import re
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request
import uuid
import wave

from pet_i18n import tr
from pet_network import build_network_opener
from pet_voice_catalog import CHINESE_VOICES

CREDENTIAL_TARGET = "TuanziDesktopPet/TencentSpeech"
ASR_ENGINES = {"zh-CN": "16k_zh", "en": "16k_en", "ms": "16k_ms"}
VOICES = {"zh-CN": CHINESE_VOICES,
          "en": ((101050, "WeJack"), (501009, "WeWinny · Large model"))}
SAMPLE_RATE, MAX_RECORD_SECONDS, MAX_SPEECH_BYTES = 16000, 55, 3000


class SpeechError(Exception):
    def __init__(self, key, **values):
        self.key, self.values = key, values
        super().__init__(key)


def error_text(error, language="zh-CN"):
    if isinstance(error, SpeechError):
        return tr(error.key, language, **error.values)
    return tr("voice.failed", language)


@dataclass(frozen=True)
class SpeechCredentials:
    secret_id: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)

    @property
    def valid(self):
        return bool(self.secret_id.strip() and self.secret_key.strip())

    def serialize(self):
        return json.dumps({"secret_id": self.secret_id.strip(), "secret_key": self.secret_key.strip()})

    @classmethod
    def parse(cls, text):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return cls(str(obj.get("secret_id") or ""), str(obj.get("secret_key") or ""))
        except (ValueError, TypeError):
            pass
        return cls()


def voice_settings(value=None):
    value = value if isinstance(value, dict) else {}
    language = value.get("recognition_language", "zh-CN")
    language = language if isinstance(language, str) and language in ASR_ENGINES else "zh-CN"
    result = {"recognition_language": language, "auto_play": value.get("auto_play") is True,
              "auto_send": value.get("auto_send", True) is True}
    for lang, key in (("zh-CN", "voice_zh"), ("en", "voice_en")):
        ids = [item[0] for item in VOICES[lang]]
        result[key] = value.get(key) if type(value.get(key)) is int and value[key] in ids else ids[0]
    for key, low, high in (("speed", -2, 2), ("volume", -10, 5)):
        try:
            number = float(value.get(key, 0))
            result[key] = max(low, min(high, number)) if math.isfinite(number) else 0.0
        except (ValueError, TypeError):
            result[key] = 0.0
    return result


def split_text(text, limit=300):
    """Conservative UTF-8 limits for both Chinese and English TextToVoice."""
    text = text.strip()
    if not text:
        raise SpeechError("voice.empty")
    if len(text.encode("utf-8")) > MAX_SPEECH_BYTES:
        raise SpeechError("voice.too_long")
    chunks, current, size = [], "", 0
    for character in text:
        width = len(character.encode("utf-8"))
        if size + width > limit:
            chunks.append(current)
            current, size = "", 0
        current += character
        size += width
        if character in "。！？!?\n" and size >= limit // 3:
            chunks.append(current)
            current, size = "", 0
    if current:
        chunks.append(current)
    return chunks


def pcm_wave(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return output.getvalue()


def read_wave(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getsampwidth() != 2 or audio.getnchannels() != 1
                    or audio.getframerate() not in (8000, 16000, 24000)
                    or audio.getcomptype() != "NONE" or audio.getnframes() == 0):
                raise ValueError("Unsupported audio format")
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != audio.getnframes() * 2:
                raise ValueError("Incomplete audio")
            return audio.getframerate(), pcm
    except (wave.Error, EOFError, ValueError, OSError) as exc:
        raise SpeechError("voice.bad_audio") from exc


class _SdkTransport:
    """Official SDK signing/models with this app's verified TLS and PAC routing."""
    def send_request(self, request):
        if request.host not in {"asr.tencentcloudapi.com", "tts.tencentcloudapi.com"}:
            raise SpeechError("voice.network")
        url = "https://" + request.host + (request.uri or "/")
        data = request.data.encode("utf-8") if isinstance(request.data, str) else request.data
        outgoing = Request(url, data=data, headers=request.header, method="POST")
        try:
            response = build_network_opener(url=url).open(outgoing, timeout=20)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise SpeechError("voice.bad_audio")
            return SimpleNamespace(status_code=response.code, content=content,
                headers={"Content-Type": "application/json"}, text=content.decode("utf-8", "replace"))


def _sdk_failure(exc):
    code = str(getattr(exc, "code", ""))
    if code.startswith("AuthFailure"):
        return SpeechError("voice.auth")
    if any(word in code for word in ("UserHasNo", "ResourceInsufficient", "ResourceSoldOut", "ServiceIsolate", "ResourceUnavailable")):
        return SpeechError("voice.quota")
    if any(word in code for word in ("NotRegistered", "NotActivated", "ServiceNotOpen")):
        return SpeechError("voice.not_enabled")
    if "Network" in code or not code:
        return SpeechError("voice.network")
    safe_code = re.sub(r"[^a-zA-Z0-9_.]", "", code)[:90]
    request_id = re.sub(r"[^a-zA-Z0-9-]", "", str(getattr(exc, "requestId", "") or ""))[:64]
    return SpeechError("voice.cloud_error", code=safe_code, request_id=request_id or "--")


class TencentSpeechClient:
    def __init__(self, credentials):
        if not credentials.valid:
            raise SpeechError("voice.credentials_missing")
        self.credentials = credentials

    def _client(self, service):
        try:
            from tencentcloud.common.credential import Credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.common.retry import NoopRetryer
            if service == "asr":
                from tencentcloud.asr.v20190614.asr_client import AsrClient
                cls = AsrClient
            else:
                from tencentcloud.tts.v20190823.tts_client import TtsClient
                cls = TtsClient
            profile = ClientProfile(httpProfile=HttpProfile(reqTimeout=20), retryer=NoopRetryer())
            client = cls(Credential(self.credentials.secret_id, self.credentials.secret_key), "", profile)
            client.request = _SdkTransport()
            return client
        except ImportError as exc:
            raise SpeechError("voice.dependencies") from exc

    def recognize(self, wav, language="zh-CN"):
        if language not in ASR_ENGINES:
            raise SpeechError("voice.language")
        rate, pcm = read_wave(wav)
        if rate != SAMPLE_RATE or len(pcm) > SAMPLE_RATE * 2 * MAX_RECORD_SECONDS:
            raise SpeechError("voice.bad_audio")
        try:
            client = self._client("asr")
            from tencentcloud.asr.v20190614.models import SentenceRecognitionRequest
            request = SentenceRecognitionRequest()
            request.EngSerViceType = ASR_ENGINES[language]
            request.SourceType, request.VoiceFormat = 1, "wav"
            request.Data = base64.b64encode(wav).decode("ascii")
            request.DataLen = len(wav)
            result = client.SentenceRecognition(request).Result
            if not result or not result.strip():
                raise SpeechError("voice.no_speech")
            return result.strip()
        except SpeechError:
            raise
        except Exception as exc:
            raise _sdk_failure(exc) from None

    def synthesize(self, text, language, options):
        if language not in VOICES:
            raise SpeechError("voice.tts_language")
        if not text.strip():
            raise SpeechError("voice.empty")
        if len(text.encode("utf-8")) > 300:
            raise SpeechError("voice.too_long")
        options = voice_settings(options)
        try:
            client = self._client("tts")
            from tencentcloud.tts.v20190823.models import TextToVoiceRequest
            request = TextToVoiceRequest()
            request.Text, request.SessionId = text, str(uuid.uuid4())
            request.ModelType = 1
            request.VoiceType = options["voice_zh" if language == "zh-CN" else "voice_en"]
            request.PrimaryLanguage = 1 if language == "zh-CN" else 2
            request.Codec, request.SampleRate = "wav", SAMPLE_RATE
            request.Speed, request.Volume = options["speed"], options["volume"]
            audio = base64.b64decode(client.TextToVoice(request).Audio, validate=True)
            read_wave(audio)
            return audio
        except SpeechError:
            raise
        except (ValueError, TypeError) as exc:
            raise SpeechError("voice.bad_audio") from exc
        except Exception as exc:
            raise _sdk_failure(exc) from None


def _sounddevice():
    try:
        import sounddevice
        return sounddevice
    except (ImportError, OSError) as exc:
        raise SpeechError("voice.dependencies") from exc


def capture_audio(cancel, finish, level=None):
    """Stop recording at silence, a second click, or 55 seconds."""
    device = _sounddevice()
    frames, speech_blocks, silence = [], 0, 0
    try:
        with device.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=1600) as stream:
            for index in range(MAX_RECORD_SECONDS * 10):
                if cancel.is_set() or finish.is_set():
                    break
                block, overflow = stream.read(1600)
                if overflow:
                    raise SpeechError("voice.input_overflow")
                block = bytes(block)
                frames.append(block)
                samples = array.array("h", block)
                rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
                if level is not None:
                    level(min(1.0, max(0.0, (rms-120)/2880)) ** .6)
                if rms >= 250:
                    speech_blocks += 1
                    silence = 0
                else:
                    silence += 1
                # Allow a two-second thinking/breathing pause before submitting.
                if (speech_blocks >= 3 and silence >= 22) or (speech_blocks == 0 and index >= 59):
                    break
    except SpeechError:
        raise
    except Exception as exc:
        raise SpeechError("voice.microphone") from exc
    if cancel.is_set():
        return b""
    if speech_blocks < 3:
        raise SpeechError("voice.no_speech")
    return pcm_wave(b"".join(frames))


class TencentSpeechInput:
    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._input_level = 0.0
        self._cancel, self._finish = threading.Event(), threading.Event()

    @property
    def listening(self):
        with self._lock:
            return self._busy

    @property
    def input_level(self):
        """Last normalized recording level; worker never touches Tk widgets."""
        with self._lock:
            return self._input_level

    def start(self, callback, credentials, options=None, phase=None):
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._input_level = 0.0
            self._cancel, self._finish = threading.Event(), threading.Event()
            cancel, finish = self._cancel, self._finish
        options = voice_settings(options)
        def publish_level(value):
            with self._lock:
                if self._cancel is cancel and not cancel.is_set() and not finish.is_set():
                    self._input_level = value
        def run():
            text, error = None, None
            try:
                if not credentials.valid:
                    raise SpeechError("voice.credentials_missing")
                wav = capture_audio(cancel, finish, publish_level)
                with self._lock:
                    self._input_level = 0.0
                if wav and not cancel.is_set():
                    if phase:
                        phase("voice.transcribing")
                    text = TencentSpeechClient(credentials).recognize(wav, options["recognition_language"])
            except Exception as exc:
                error = exc
            finally:
                with self._lock:
                    self._busy = False
                    self._input_level = 0.0
            if not cancel.is_set():
                callback(text, error)
        threading.Thread(target=run, name="pawpal-asr", daemon=True).start()
        return True

    def finish(self):
        self._finish.set()
        with self._lock:
            self._input_level = 0.0

    def stop(self):
        self._cancel.set()
        self._finish.set()
        with self._lock:
            self._input_level = 0.0


class TencentSpeechOutput:
    """Single worker, one pending job, cancellable audio and bounded RAM cache."""
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = self._thread = self._stream = None
        self._cancel = threading.Event()
        self._cache = OrderedDict()

    def play(self, text, language, credentials, options, callback):
        options = voice_settings(options)
        split_text(text)
        if language not in VOICES:
            raise SpeechError("voice.tts_language")
        if not credentials.valid:
            raise SpeechError("voice.credentials_missing")
        self.stop()
        with self._lock:
            self._pending = (text, language, credentials, options, callback)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="pawpal-tts", daemon=True)
                self._thread.start()

    def stop(self):
        with self._lock:
            self._pending = None
            self._cancel.set()
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def _play_wave(self, audio, cancel):
        rate, pcm = read_wave(audio)
        try:
            with _sounddevice().RawOutputStream(samplerate=rate, channels=1, dtype="int16") as stream:
                with self._lock:
                    self._stream = stream
                try:
                    for offset in range(0, len(pcm), rate // 5):
                        if cancel.is_set():
                            break
                        stream.write(pcm[offset:offset + rate // 5])
                finally:
                    with self._lock:
                        self._stream = None
        except SpeechError:
            raise
        except Exception as exc:
            if not cancel.is_set():
                raise SpeechError("voice.speaker") from exc

    def _run(self):
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._thread = None
                    return
                self._cancel = cancel = threading.Event()
            text, language, credentials, options, callback = job
            try:
                client = TencentSpeechClient(credentials)
                for chunk in split_text(text):
                    if cancel.is_set():
                        break
                    identity = hashlib.sha256(credentials.serialize().encode()).digest()
                    key = (identity, chunk, language, options["voice_zh" if language == "zh-CN" else "voice_en"],
                           options["speed"], options["volume"])
                    audio = self._cache.get(key)
                    if audio is None:
                        callback("voice.synthesizing", None)
                        audio = client.synthesize(chunk, language, options)
                        if cancel.is_set():
                            break
                        self._cache[key] = audio
                        while len(self._cache) > 12 or sum(map(len, self._cache.values())) > 8 * 1024 * 1024:
                            self._cache.popitem(last=False)
                    else:
                        self._cache.move_to_end(key)
                    if cancel.is_set():
                        break
                    callback("voice.playing", None)
                    self._play_wave(audio, cancel)
                if not cancel.is_set():
                    callback("voice.finished", None)
            except Exception as exc:
                if not cancel.is_set():
                    callback(None, exc)
