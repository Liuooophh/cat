"""Speech tests use synthetic audio and fake SDK responses; no microphone/cloud."""
from __future__ import annotations
import array
import base64
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pet_cloud_voice as voice
from pet_voice_settings import save_speech_configuration

CREDENTIALS = voice.SpeechCredentials("test-identifier", "test-secret")
AUDIO = voice.pcm_wave(array.array("h", [1000] * 1600).tobytes())


class SpeechSettingsTests(unittest.TestCase):
    def test_defaults_are_silent_and_legacy_values_cannot_enable_audio(self):
        self.assertFalse(voice.voice_settings()["auto_play"])
        self.assertFalse(voice.voice_settings({"auto_play": "true"})["auto_play"])
        self.assertFalse(voice.voice_settings({"voice_reply_enabled": True})["auto_play"])

    def test_corrupt_settings_are_normalized(self):
        options = voice.voice_settings({"recognition_language": [], "voice_zh": True,
                                        "speed": float("nan"), "volume": "invalid"})
        self.assertEqual(options["recognition_language"], "zh-CN")
        self.assertEqual(options["voice_zh"], 101001)
        self.assertEqual(options["speed"], 0)
        self.assertEqual(options["volume"], 0)

    def test_credentials_round_trip_without_repr_exposure(self):
        self.assertEqual(voice.SpeechCredentials.parse(CREDENTIALS.serialize()), CREDENTIALS)
        self.assertNotIn("test-secret", repr(CREDENTIALS))
        for value in ("", "not json", "[]", "null"):
            self.assertFalse(voice.SpeechCredentials.parse(value).valid)

    def app(self, fail=False):
        app = SimpleNamespace(settings={"language": "en"}, _load_api_key=Mock(return_value="old-credential"),
                              _store_api_key=Mock(), _save_settings_file=Mock())
        if fail:
            app._save_settings_file.side_effect = OSError("disk failure")
        return app

    def test_credentials_only_go_to_vault(self):
        app = self.app()
        save_speech_configuration(app, CREDENTIALS, {"auto_play": True})
        app._store_api_key.assert_called_once_with(CREDENTIALS.serialize(), voice.CREDENTIAL_TARGET)
        self.assertNotIn("test-secret", json.dumps(app.settings))
        self.assertNotIn("test-identifier", json.dumps(app.settings))
        self.assertTrue(app.settings["tencent_speech"]["auto_play"])

    def test_save_failure_restores_credential_and_settings(self):
        app = self.app(True)
        with self.assertRaises(OSError):
            save_speech_configuration(app, CREDENTIALS, {})
        self.assertEqual(app.settings, {"language": "en"})
        self.assertEqual(app._store_api_key.call_args.args, ("old-credential", voice.CREDENTIAL_TARGET))

    def test_clear_both_fields_deletes_vault_entry(self):
        app = self.app()
        save_speech_configuration(app, voice.SpeechCredentials(), {})
        app._store_api_key.assert_called_once_with("", voice.CREDENTIAL_TARGET)

    def test_partial_credentials_do_not_change_settings(self):
        app = self.app()
        with self.assertRaises(voice.SpeechError):
            save_speech_configuration(app, voice.SpeechCredentials("id", ""), {})
        app._store_api_key.assert_not_called()


class AudioTests(unittest.TestCase):
    def test_wav_round_trip_and_validation(self):
        rate, pcm = voice.read_wave(AUDIO)
        self.assertEqual(rate, 16000)
        self.assertEqual(len(pcm), 3200)
        for value in (b"", b"not audio", AUDIO[:40], AUDIO[:-100]):
            with self.subTest(value=value[:10]), self.assertRaises(voice.SpeechError):
                voice.read_wave(value)

    def test_utf8_chunks_preserve_text_and_all_have_safe_sizes(self):
        text = "你好。" * 100 + "Hello world! " * 30
        chunks = voice.split_text(text)
        self.assertEqual("".join(chunks), text.strip())
        self.assertTrue(all(0 < len(chunk.encode()) <= 300 for chunk in chunks))
        for bad in ("", " ", "猫" * 1001):
            with self.assertRaises(voice.SpeechError):
                voice.split_text(bad)

    def stream(self, blocks):
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.side_effect = [(block, False) for block in blocks]
        return stream

    def test_silence_stops_without_a_cloud_request(self):
        stream = self.stream([b"\0" * 3200] * 60)
        with patch.object(voice, "_sounddevice", return_value=SimpleNamespace(RawInputStream=Mock(return_value=stream))):
            with self.assertRaises(voice.SpeechError) as raised:
                voice.capture_audio(threading.Event(), threading.Event())
        self.assertEqual(raised.exception.key, "voice.no_speech")
        self.assertEqual(stream.read.call_count, 60)

    def test_voice_then_pause_finishes_and_releases_microphone(self):
        block = array.array("h", [1000] * 1600).tobytes()
        stream = self.stream([block] * 4 + [b"\0" * 3200] * 22)
        with patch.object(voice, "_sounddevice", return_value=SimpleNamespace(RawInputStream=Mock(return_value=stream))):
            result = voice.capture_audio(threading.Event(), threading.Event())
        self.assertEqual(len(voice.read_wave(result)[1]), 3200 * 26)
        stream.__exit__.assert_called_once()

    def test_short_pause_does_not_end_a_sentence(self):
        block = array.array("h", [1000] * 1600).tobytes()
        blocks = [block] * 4 + [b"\0" * 3200] * 12 + [block] * 4 + [b"\0" * 3200] * 22
        stream = self.stream(blocks)
        with patch.object(voice, "_sounddevice", return_value=SimpleNamespace(RawInputStream=Mock(return_value=stream))):
            result = voice.capture_audio(threading.Event(), threading.Event())
        self.assertEqual(len(voice.read_wave(result)[1]), 3200 * len(blocks))

    def test_cancelled_recording_returns_no_upload_payload(self):
        cancel = threading.Event(); cancel.set()
        stream = self.stream([])
        with patch.object(voice, "_sounddevice", return_value=SimpleNamespace(RawInputStream=Mock(return_value=stream))):
            self.assertEqual(voice.capture_audio(cancel, threading.Event()), b"")
        stream.read.assert_not_called()

    def test_actual_pcm_energy_drives_meter_without_an_extra_stream(self):
        quiet = array.array("h", [500] * 1600).tobytes()
        loud = array.array("h", [3000] * 1600).tobytes()
        stream = self.stream([quiet]*3+[loud]*3+[b"\0"*3200]*22)
        factory = Mock(return_value=stream)
        levels = []
        with patch.object(voice, "_sounddevice", return_value=SimpleNamespace(RawInputStream=factory)):
            voice.capture_audio(threading.Event(), threading.Event(), levels.append)
        factory.assert_called_once()
        self.assertGreater(levels[3], levels[0])
        self.assertEqual(levels[3], 1)
        self.assertEqual(levels[-1], 0)
        self.assertTrue(all(0 <= value <= 1 for value in levels))


class SdkTests(unittest.TestCase):
    def setUp(self):
        try:
            from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        except ImportError:
            self.skipTest("Install speech SDK dependencies")
        self.SDKError = TencentCloudSDKException
        self.client = voice.TencentSpeechClient(CREDENTIALS)

    def test_each_language_uses_its_own_engine_and_exact_upload_length(self):
        for language, engine in voice.ASR_ENGINES.items():
            sdk = Mock(); sdk.SentenceRecognition.return_value.Result = " transcript "
            with patch.object(self.client, "_client", return_value=sdk):
                self.assertEqual(self.client.recognize(AUDIO, language), "transcript")
            request = sdk.SentenceRecognition.call_args.args[0]
            self.assertEqual(request.EngSerViceType, engine)
            self.assertEqual(base64.b64decode(request.Data), AUDIO)
            self.assertEqual(request.DataLen, len(AUDIO))
            self.assertEqual(request.SourceType, 1)

    def test_tts_uses_selected_voice_and_validates_audio(self):
        sdk = Mock(); sdk.TextToVoice.return_value.Audio = base64.b64encode(AUDIO).decode()
        with patch.object(self.client, "_client", return_value=sdk):
            self.assertEqual(self.client.synthesize("Hello", "en", {}), AUDIO)
        request = sdk.TextToVoice.call_args.args[0]
        self.assertEqual((request.VoiceType, request.PrimaryLanguage, request.Codec), (101050, 2, "wav"))

    def test_malay_tts_and_oversized_input_do_not_make_paid_requests(self):
        with patch.object(self.client, "_client") as sdk:
            for text, language in (("Hello", "ms"), ("猫" * 101, "zh-CN"), ("", "en")):
                with self.assertRaises(voice.SpeechError):
                    self.client.synthesize(text, language, {})
            sdk.assert_not_called()

    def test_errors_never_echo_keys_or_audio_bodies(self):
        sdk = Mock(); sdk.TextToVoice.side_effect = self.SDKError("AuthFailure.SecretIdNotFound", "test-secret private audio", "request123")
        with patch.object(self.client, "_client", return_value=sdk), self.assertRaises(voice.SpeechError) as caught:
            self.client.synthesize("Hello", "en", {})
        self.assertEqual(caught.exception.key, "voice.auth")
        self.assertNotIn("test-secret", voice.error_text(caught.exception))
        self.assertNotIn("private audio", voice.error_text(caught.exception))

    def test_sdk_transport_is_signed_and_reuses_verified_app_network(self):
        response = Mock(code=200)
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
        response.read.return_value = json.dumps({"Response": {"Result":"hello","RequestId":"request-id"}}).encode()
        opener = Mock(); opener.open.return_value = response
        with patch.object(voice, "build_network_opener", return_value=opener):
            self.assertEqual(self.client.recognize(AUDIO, "en"), "hello")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://asr.tencentcloudapi.com/")
        self.assertIn("TC3-HMAC-SHA256", request.get_header("Authorization"))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 20)

    def test_default_sdk_retryer_does_not_repeat_paid_calls(self):
        from tencentcloud.common.retry import NoopRetryer
        sdk = self.client._client("asr")
        self.assertIsInstance(sdk.profile.retryer, NoopRetryer)


class CancellationTests(unittest.TestCase):
    def test_input_meter_resets_on_cancel_and_late_blocks_cannot_restore_it(self):
        ready, release, finished = threading.Event(), threading.Event(), threading.Event()
        service = voice.TencentSpeechInput()
        def capture(cancel, finish, level):
            level(.8)
            ready.set()
            release.wait(2)
            level(.9)
            return b""
        with patch.object(voice, "capture_audio", side_effect=capture):
            self.assertTrue(service.start(lambda *_: finished.set(), CREDENTIALS))
            self.assertTrue(ready.wait(2))
            self.assertEqual(service.input_level, .8)
            service.stop()
            self.assertEqual(service.input_level, 0)
            release.set()
            # No callback is expected after cancellation; the worker only cleans up.
            deadline = time.monotonic()+2
            while service.listening and time.monotonic() < deadline:
                finished.wait(.01)
        self.assertFalse(service.listening)
        self.assertFalse(finished.is_set())
        self.assertEqual(service.input_level, 0)

    def test_meter_resets_before_cloud_recognition_and_after_failure(self):
        service = voice.TencentSpeechInput()
        done = threading.Event()
        def capture(cancel, finish, level):
            level(.7)
            return AUDIO
        def recognize(*_args):
            self.assertEqual(service.input_level, 0)
            raise voice.SpeechError("voice.network")
        with patch.object(voice, "capture_audio", side_effect=capture), patch.object(voice.TencentSpeechClient, "recognize", side_effect=recognize):
            service.start(lambda *_: done.set(), CREDENTIALS)
            self.assertTrue(done.wait(2))
        self.assertEqual(service.input_level, 0)

    def test_cancel_inflight_asr_suppresses_late_result_and_overlap(self):
        entered, release = threading.Event(), threading.Event()
        service = voice.TencentSpeechInput(); callback = Mock()
        def recognize(*_args):
            entered.set(); release.wait(2); return "late text"
        with patch.object(voice, "capture_audio", return_value=AUDIO), patch.object(voice.TencentSpeechClient, "recognize", side_effect=recognize):
            self.assertTrue(service.start(callback, CREDENTIALS))
            self.assertTrue(entered.wait(2))
            self.assertFalse(service.start(callback, CREDENTIALS))
            service.stop(); release.set()
            deadline = time.monotonic() + 2
            while service.listening and time.monotonic() < deadline:
                time.sleep(.01)
            callback.assert_not_called()
            self.assertFalse(service.listening)

    def test_cancel_inflight_tts_never_starts_late_playback(self):
        entered, release = threading.Event(), threading.Event()
        player = voice.TencentSpeechOutput()
        def synthesize(*_args):
            entered.set(); release.wait(2); return AUDIO
        with patch.object(voice.TencentSpeechClient, "synthesize", side_effect=synthesize), patch.object(player, "_play_wave") as playback:
            player.play("hello", "en", CREDENTIALS, {}, Mock())
            self.assertTrue(entered.wait(2))
            thread = player._thread
            player.stop(); release.set(); thread.join(2)
            self.assertFalse(thread.is_alive())
            playback.assert_not_called()

    def test_repeat_play_uses_cache_but_different_credentials_do_not(self):
        player = voice.TencentSpeechOutput()
        with patch.object(voice.TencentSpeechClient, "synthesize", return_value=AUDIO) as cloud, patch.object(player, "_play_wave"):
            for options, credentials in (({}, CREDENTIALS), ({"auto_play":True}, CREDENTIALS), ({}, voice.SpeechCredentials("other", "other-secret"))):
                done = threading.Event()
                player.play("hello", "en", credentials, options, lambda phase, error: done.set() if phase == "voice.finished" or error else None)
                self.assertTrue(done.wait(2))
            self.assertEqual(cloud.call_count, 2)


if __name__ == "__main__":
    unittest.main()
