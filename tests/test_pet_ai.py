from __future__ import annotations

import io
import json
import socket
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch

from pet_ai import (ChatError, credential_target, legacy_credential_target, migrate_model_name,
                    normalize_chat_url, provider_name, redact_detail, request_chat, test_connection as connection_test)


class ChatClientTests(unittest.TestCase):
    def request(self, body, **kwargs):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(body).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch("pet_ai.build_opener", return_value=opener):
            answer = request_chat("http://localhost:11434", "custom-reasoning-model", "", [{"role": "user", "content": "hello"}], **kwargs)
        return answer, opener.open.call_args

    def test_normalizes_supported_bases_and_complete_urls(self):
        cases = {
            " https://api.openai.com/v1/ ": "https://api.openai.com/v1/chat/completions",
            "https://api.deepseek.com": "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/v1": "https://api.deepseek.com/v1/chat/completions",
            "http://localhost:11434": "http://localhost:11434/v1/chat/completions",
            "https://example.com/custom/v3": "https://example.com/custom/v3/chat/completions",
            "http://localhost:8000/v1/chat/completions/": "http://localhost:8000/v1/chat/completions",
        }
        for source, expected in cases.items():
            self.assertEqual(normalize_chat_url(source), expected)
        for invalid in ("", "example.com", "ftp://test", "https://user:pass@test", "https://test?q=secret", "http://test:99999", "https://test\n/"):
            with self.assertRaises(ChatError) as caught:
                normalize_chat_url(invalid)
            self.assertEqual(caught.exception.code, "invalid_endpoint")

    def test_does_not_infer_model_parameters_from_names(self):
        answer, call = self.request({"choices": [{"message": {"content": "  Hi  "}}]})
        self.assertEqual(answer, "Hi")
        payload = json.loads(call.args[0].data)
        self.assertEqual(set(payload), {"model", "messages", "stream"})
        self.assertNotIn("Authorization", call.args[0].headers)

    def test_content_blocks_and_refusal(self):
        answer, _ = self.request({"choices": [{"message": {"content": [{"type": "text", "text": "one"}, {"type": "text", "text": " two"}]}}]})
        self.assertEqual(answer, "one two")
        answer, _ = self.request({"choices": [{"message": {"content": None, "refusal": "Cannot answer"}}]})
        self.assertEqual(answer, "Cannot answer")

    def test_empty_and_invalid_responses_are_not_success(self):
        for body, code in (({"choices": [{"message": {"content": None}}]}, "empty_response"), ({}, "invalid_response"), ({"choices": []}, "invalid_response")):
            with self.assertRaises(ChatError) as caught:
                self.request(body)
            self.assertEqual(caught.exception.code, code)

    def test_http_codes_and_provider_details_are_safely_exposed(self):
        cases = [(401, "invalid_api_key", "authentication"), (403, "forbidden", "permission"),
                 (429, "insufficient_quota", "quota"), (429, "rate_limit", "rate_limit"),
                 (404, "model_not_found", "model_not_found"), (404, "not_found", "not_found"),
                 (503, "busy", "server"), (400, "invalid_parameter", "bad_request")]
        for status, service_code, expected in cases:
            body = json.dumps({"error": {"code": service_code, "message": "Problem with Bearer TOP_SECRET"}}).encode()
            error = HTTPError("https://test", status, "Error", {}, io.BytesIO(body))
            opener = Mock()
            opener.open.side_effect = error
            with patch("pet_ai.build_opener", return_value=opener), self.assertRaises(ChatError) as caught:
                request_chat("https://test", "model", "TOP_SECRET", [{"role": "user", "content": "hi"}])
            result = caught.exception
            self.assertEqual(result.code, expected)
            self.assertEqual(result.status, status)
            self.assertNotIn("TOP_SECRET", str(result))
            self.assertIn(str(status), result.localized("ms"))

    def test_network_timeout_and_no_retry(self):
        for error, code in ((URLError("DNS failed"), "network"), (URLError(socket.timeout()), "timeout"), (TimeoutError(), "timeout")):
            opener = Mock()
            opener.open.side_effect = error
            with patch("pet_ai.build_opener", return_value=opener), self.assertRaises(ChatError) as caught:
                request_chat("https://test", "model", "", [{"role": "user", "content": "hi"}])
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(opener.open.call_count, 1)

    def test_connection_test_uses_same_request(self):
        with patch("pet_ai.request_chat", return_value="OK") as request:
            self.assertEqual(connection_test("http://test", "m", "secret"), "OK")
        request.assert_called_once_with("http://test", "m", "secret", [{"role": "user", "content": "Reply with OK only."}], 30)

    def test_redaction_and_host_detection(self):
        result = redact_detail("<b>sk-1234567890</b> Authorization: abc\nBearer xyz password TOKEN", "TOKEN")
        self.assertNotIn("sk-1234567890", result)
        self.assertNotIn("abc", result)
        self.assertNotIn("xyz", result)
        self.assertNotIn("TOKEN", result)
        self.assertEqual(provider_name("https://api.openai.com/v1"), "OpenAI")
        self.assertEqual(provider_name("https://api.openai.com.attacker.test/v1"), "Custom / Ollama")

    def test_deepseek_defaults_and_retired_models_use_current_api(self):
        from pet_ai import DEFAULT_MODELS
        self.assertEqual(DEFAULT_MODELS["DeepSeek"], "deepseek-flash")
        for name in ("deepseek-chat", "deepseek-reasoner"):
            self.assertEqual(migrate_model_name("https://api.deepseek.com/v1", name), "deepseek-flash")
        self.assertEqual(migrate_model_name("https://custom.test/v1", "deepseek-chat"), "deepseek-chat")
        self.assertEqual(migrate_model_name("https://api.deepseek.com", "deepseek-v4-pro"), "deepseek-v4-pro")

    def test_deepseek_connection_is_bounded_on_shared_request_path(self):
        with patch("pet_ai.request_chat", return_value="OK") as request:
            connection_test("https://api.deepseek.com", "deepseek-chat", "fixture", 30)
        request.assert_called_once_with("https://api.deepseek.com", "deepseek-flash", "fixture",
            [{"role": "user", "content": "Reply with OK only."}], 30, max_tokens=16)

    def test_deepseek_payload_disables_default_thinking_and_bounds_test(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"choices":[{"message":{"content":"OK"}}]}'
        opener = Mock()
        opener.open.return_value = response
        with patch("pet_ai.build_opener", return_value=opener):
            self.assertEqual(connection_test("https://api.deepseek.com/v1", "deepseek-chat", "fixture"), "OK")
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 16)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(opener.open.call_count, 1)

    def test_canonical_credentials_preserve_equivalent_endpoint_identity(self):
        plain = "https://api.deepseek.com"
        explicit = "https://API.DEEPSEEK.COM:443/v1/chat/completions/"
        self.assertEqual(credential_target(plain), credential_target(explicit))
        self.assertNotEqual(legacy_credential_target(plain), legacy_credential_target(explicit))
        self.assertEqual(normalize_chat_url(explicit), "https://api.deepseek.com/v1/chat/completions")
        for wrong in ("http://api.deepseek.com", "https://api.deepseek.com/anthropic", "https://api.deepseek.com/responses"):
            with self.assertRaises(ChatError):
                normalize_chat_url(wrong)


class SettingsPersistenceTests(unittest.TestCase):
    def app(self):
        from types import SimpleNamespace
        app = SimpleNamespace(settings={"endpoint": "https://API.DEEPSEEK.COM:443/v1", "model": "deepseek-chat"},
                              _credential_target=credential_target,
                              _legacy_key_endpoint="https://API.DEEPSEEK.COM:443/v1")
        credentials = {credential_target(app.settings["endpoint"]): "canonical-fixture",
                       legacy_credential_target(app.settings["endpoint"]): "old-port-fixture",
                       "shared": "shared-fixture"}
        app._load_api_key = lambda target="shared": credentials.get(target, "")
        def store(value, target="shared"):
            if value:
                credentials[target] = value
            else:
                credentials.pop(target, None)
        app._store_api_key = store
        app._save_settings_file = Mock()
        return app, credentials

    def test_clear_key_removes_legacy_port_and_shared_slots(self):
        from pet_settings import save_ai_configuration
        app, credentials = self.app()
        save_ai_configuration(app, "https://api.deepseek.com", "deepseek-chat", "", {})
        self.assertEqual(credentials, {})
        self.assertIsNone(app._legacy_key_endpoint)
        self.assertEqual(app.settings["model"], "deepseek-flash")
        self.assertEqual(app.settings["ai_profiles"]["DeepSeek"]["endpoint"], "https://api.deepseek.com")
        self.assertNotIn("key", json.dumps(app.settings).lower())

    def test_failed_save_restores_settings_and_all_credential_slots(self):
        from pet_settings import save_ai_configuration
        app, credentials = self.app()
        old_settings, old_credentials = dict(app.settings), dict(credentials)
        app._save_settings_file.side_effect = OSError("disk full fixture")
        with self.assertRaises(OSError):
            save_ai_configuration(app, "https://api.deepseek.com", "deepseek-flash", "new-fixture", {})
        self.assertEqual(app.settings, old_settings)
        self.assertEqual(credentials, old_credentials)
        self.assertIsNotNone(app._legacy_key_endpoint)

    def test_profile_uses_actual_endpoint_provider_and_retains_other_profiles(self):
        from pet_settings import save_ai_configuration
        app, _ = self.app()
        profiles = {"OpenAI": {"endpoint": "https://api.openai.com/v1", "model": "gpt-5-mini"}}
        save_ai_configuration(app, "https://api.deepseek.com/v1", "deepseek-flash", "new-fixture", profiles)
        self.assertEqual(app.settings["ai_profiles"]["OpenAI"], profiles["OpenAI"])
        self.assertIn("DeepSeek", app.settings["ai_profiles"])


if __name__ == "__main__":
    unittest.main()
