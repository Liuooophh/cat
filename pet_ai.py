"""Provider-neutral Chat Completions requests and safe, useful errors."""

from __future__ import annotations

import json
import hashlib
import re
import socket
from collections.abc import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request

from pet_i18n import tr
from pet_network import build_network_opener as build_opener, network_failure, safe_detail

DEFAULT_ENDPOINTS = {"OpenAI": "https://api.openai.com/v1", "DeepSeek": "https://api.deepseek.com"}
# Verified against the official model/API documentation on 2026-10-24.
# Defaults are editable and do not assert access for a particular account.
DEFAULT_MODELS = {"OpenAI": "gpt-5-mini", "DeepSeek": "deepseek-flash"}
RETIRED_DEEPSEEK_MODELS = frozenset({"deepseek-chat", "deepseek-reasoner"})
MAX_RESPONSE_BYTES = 2_000_000


class ChatError(Exception):
    def __init__(self, code: str, detail: str = "", status: int | None = None) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(self.localized())

    def localized(self, language: str = "zh-CN") -> str:
        message = tr("ai." + self.code, language)
        if self.status is not None:
            message += f" (HTTP {self.status})"
        return message + ("\n" + self.detail if self.detail else "")


def provider_name(endpoint: str) -> str:
    try:
        host = (urlsplit(str(endpoint)).hostname or "").lower()
    except ValueError:
        return "Custom / Ollama"
    if host == "api.openai.com":
        return "OpenAI"
    if host == "api.deepseek.com":
        return "DeepSeek"
    return "Custom / Ollama"


def normalize_chat_url(endpoint: str) -> str:
    try:
        parts = urlsplit(str(endpoint).strip())
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or any(c.isspace() for c in parts.netloc)):
            raise ValueError("Invalid URL")
        # Evaluating port catches malformed and out-of-range values.
        _ = parts.port
        if parts.hostname.lower() in {"api.openai.com", "api.deepseek.com"} and parts.scheme.lower() != "https":
            raise ValueError("Official services require HTTPS")
        path = parts.path.rstrip("/")
        if any(ord(c) < 32 for c in str(endpoint)):
            raise ValueError("Invalid URL")
        if path.endswith("/chat/completions"):
            pass
        elif not path:
            path = "/chat/completions" if parts.hostname.lower() == "api.deepseek.com" else "/v1/chat/completions"
        else:
            path += "/chat/completions"
        host = parts.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port
        if port is not None and (parts.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
            host += ":" + str(port)
        if parts.hostname.lower() == "api.deepseek.com" and path not in {"/chat/completions", "/v1/chat/completions", "/beta/chat/completions"}:
            raise ChatError("invalid_endpoint", "DeepSeek OpenAI-compatible base: https://api.deepseek.com (or /v1). Do not use the website, /anthropic, /responses, or /messages endpoint here.")
        return urlunsplit((parts.scheme.lower(), host, path, "", ""))
    except (ValueError, AttributeError) as exc:
        raise ChatError("invalid_endpoint") from exc


def migrate_model_name(endpoint: str, model: str) -> str:
    name = str(model or "").strip()
    return DEFAULT_MODELS["DeepSeek"] if provider_name(endpoint) == "DeepSeek" and name in RETIRED_DEEPSEEK_MODELS else name


def credential_target(endpoint: str) -> str:
    """One credential identity for equivalent host/default-port spellings."""
    parts = urlsplit(normalize_chat_url(endpoint))
    return "PawpalCat/AI/" + hashlib.sha256(parts.netloc.lower().encode("utf-8")).hexdigest()[:24]


def legacy_credential_target(endpoint: str) -> str:
    host = (urlsplit(endpoint).netloc or endpoint).lower()
    return "PawpalCat/AI/" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:24]


def redact_detail(value: object, key: str = "") -> str:
    """Return bounded plain text with keys and credentials removed."""
    return safe_detail(value, key)


def _error_from_http(exc: HTTPError, key: str) -> ChatError:
    detail = ""
    service_code = ""
    try:
        raw = exc.read(16_384).decode("utf-8", errors="replace")
        body = json.loads(raw)
        error = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("detail") or "")
            service_code = str(error.get("code") or error.get("type") or "").lower()
        elif isinstance(error, str):
            detail = error
    except (OSError, ValueError, TypeError):
        # HTML proxy pages are frequently huge and unsafe to display verbatim.
        pass
    lookup = (service_code + " " + detail).lower()
    if exc.code == 401:
        code = "authentication"
    elif exc.code == 402 or any(token in lookup for token in ("insufficient_quota", "insufficient_balance", "insufficient balance", "credit balance", "billing_hard_limit")):
        code = "quota"
    elif exc.code == 403:
        code = "permission"
    elif exc.code == 407:
        code = "network"
        detail = "The configured proxy requires authentication (HTTP 407). Check its credentials."
    elif any(token in lookup for token in ("model_not_found", "model does not exist", "model not found", "unknown model", "invalid model")):
        code = "model_not_found"
    elif exc.code == 404:
        code = "not_found"
    elif 300 <= exc.code < 400:
        code = "invalid_endpoint"
        detail = "The API redirected the request. Use the final API address; credentials were not forwarded."
    elif exc.code == 429:
        code = "rate_limit"
    elif exc.code in {408, 504}:
        code = "timeout"
    elif exc.code >= 500:
        code = "server"
    else:
        code = "bad_request"
    return ChatError(code, redact_detail(detail, key), exc.code)


class _NoRedirect(HTTPRedirectHandler):
    # Never send API credentials to a redirect destination or turn POST into GET.
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_chat(endpoint: str, model: str, key: str,
                 messages: Sequence[Mapping[str, object]], timeout: float = 50,
                 *, max_tokens: int | None = None) -> str:
    """Send one non-streamed request; no retries that could duplicate API charges."""
    url = normalize_chat_url(endpoint)
    original_model = str(model).strip()
    model = migrate_model_name(url, original_model)
    key = str(key or "").strip()
    if not model:
        raise ChatError("invalid_model")
    if not messages or isinstance(messages, (str, bytes)):
        raise ChatError("invalid_messages")
    safe_messages: list[dict[str, object]] = []
    for message in messages:
        if (not isinstance(message, Mapping) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}
                or not isinstance(message.get("content"), (str, list))):
            raise ChatError("invalid_messages")
        safe_messages.append(dict(message))
    try:
        duration = max(1.0, min(180.0, float(timeout)))
        content = {"model": model, "messages": safe_messages, "stream": False}
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 16_384:
                raise ChatError("bad_request", "Invalid output token limit.")
            content["max_completion_tokens" if provider_name(url) == "OpenAI" else "max_tokens"] = max_tokens
        if provider_name(url) == "DeepSeek" and model in {"deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
            # The official default is high-effort thinking. A short desktop
            # conversation should not wait for a large hidden reasoning pass.
            content["thinking"] = {"type": "enabled" if original_model == "deepseek-reasoner" else "disabled"}
        payload = json.dumps(content, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "PawpalCat/5"}
        if key:
            if "\r" in key or "\n" in key:
                raise ChatError("authentication")
            headers["Authorization"] = "Bearer " + key
        request = Request(url, data=payload, headers=headers, method="POST")
        with build_opener(_NoRedirect(), url=url).open(request, timeout=duration) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ChatError("invalid_response")
        body = json.loads(raw.decode("utf-8-sig"))
    except ChatError:
        raise
    except HTTPError as exc:
        raise _error_from_http(exc, key) from exc
    except (socket.timeout, TimeoutError) as exc:
        _, detail = network_failure(exc, key)
        raise ChatError("timeout", detail) from exc
    except URLError as exc:
        kind, detail = network_failure(exc, key)
        raise ChatError("timeout" if kind == "timeout" else "network", detail) from exc
    except OSError as exc:
        kind, detail = network_failure(exc, key)
        raise ChatError("timeout" if kind == "timeout" else "network", detail) from exc
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ChatError("invalid_response") from exc
    try:
        message = body["choices"][0]["message"]
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict) and isinstance(part.get("text"), str))
        if not isinstance(content, str) or not content.strip():
            refusal = message.get("refusal")
            if isinstance(refusal, str) and refusal.strip():
                return refusal.strip()
            raise ChatError("empty_response")
        return content.strip()
    except ChatError:
        raise
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ChatError("invalid_response") from exc


def test_connection(endpoint: str, model: str, key: str, timeout: float = 30) -> str:
    """Real minimal request, used only after the user selects Test connection."""
    if provider_name(endpoint) == "DeepSeek":
        return request_chat(endpoint, migrate_model_name(endpoint, model), key,
                            [{"role": "user", "content": "Reply with OK only."}], timeout,
                            max_tokens=16)
    return request_chat(endpoint, model, key, [{"role": "user", "content": "Reply with OK only."}], timeout)
