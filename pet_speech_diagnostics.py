"""Finite speech checks. Live API calls require the explicit --live flag."""
from pathlib import Path
import argparse
import json
import sys

from pet_cloud_voice import (TencentSpeechClient, SpeechCredentials, CREDENTIAL_TARGET,
                             pcm_wave, read_wave, error_text, ASR_ENGINES, voice_settings)


def run_diagnostics(load_credential, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech-diagnostics", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Use saved credentials for two tiny, potentially paid API requests")
    args = parser.parse_args(argv)
    report = {"frozen": bool(getattr(sys, "frozen", False)), "microphone_recorded": False,
              "audio_played": False, "credentials_read": False, "cloud_requests": 0}
    code = 0
    try:
        import sounddevice
        from tencentcloud.asr.v20190614.models import SentenceRecognitionRequest
        from tencentcloud.tts.v20190823.models import TextToVoiceRequest
        client = TencentSpeechClient(SpeechCredentials("diagnostic-id", "diagnostic-secret"))
        assert client._client("asr") and client._client("tts")
        assert SentenceRecognitionRequest() and TextToVoiceRequest()
        devices = sounddevice.query_devices()
        report["components"] = {"status": "ok", "portaudio_version": sounddevice.get_portaudio_version()[1],
                                "input_devices": sum(d["max_input_channels"] > 0 for d in devices),
                                "output_devices": sum(d["max_output_channels"] > 0 for d in devices),
                                "recognition_languages": list(ASR_ENGINES), "auto_play_default": voice_settings()["auto_play"]}
        if args.live:
            report["credentials_read"] = True
            credentials = SpeechCredentials.parse(load_credential(CREDENTIAL_TARGET))
            if not credentials.valid:
                raise ValueError("Configure Tencent credentials in speech settings first")
            client = TencentSpeechClient(credentials)
            report["cloud_requests"] += 1
            audio = client.synthesize("你好", "zh-CN", {})
            rate, pcm = read_wave(audio)
            report["tts"] = {"status": "ok", "duration_seconds": len(pcm)/rate/2, "sample_rate":rate}
            report["cloud_requests"] += 1
            report["asr"] = {"status":"ok", "synthetic_transcript":client.recognize(audio, "zh-CN")}
    except Exception as exc:
        report["error"] = error_text(exc)
        report["error_type"] = type(exc).__name__
        code = 1
    args.speech_diagnostics.parent.mkdir(parents=True, exist_ok=True)
    args.speech_diagnostics.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return code
