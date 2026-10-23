"""Shared verified-TLS urllib transport with explicit proxy diagnostics.

Uses configured Windows/environment proxies and never retries through a direct
connection. No credentials, proxy addresses or user settings are logged here.
"""
from __future__ import annotations

import os
import ctypes
import fnmatch
import re
import socket
import ssl
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener, getproxies, proxy_bypass

_PAC_CACHE: dict[tuple[str, str, bool], tuple[float, dict[str, str]]] = {}
_PAC_LOCK = threading.Lock()


class NetworkConfigurationError(OSError):
    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        super().__init__(detail)


def safe_detail(value: object, secret: str = "", limit: int = 500) -> str:
    text = str(value or "")
    if secret:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)bearer\s+[^\s,;\"'<>]+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}\b", "[redacted]", text)
    text = re.sub(r"(?i)(api[_ -]?key|authorization|access[_ -]?token|password)(\s*[:=]\s*)[^\s,;\"'<>]+", r"\1\2[redacted]", text)
    # Includes SOCKS proxy credentials. Do not expose URL queries either.
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@", "proxy://[redacted]@", text, flags=re.I)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())[:limit]


def _windows_pac_configured() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            return bool(winreg.QueryValueEx(key, "AutoConfigURL")[0])
    except OSError:
        return False


def _proxy_from_windows_result(url: str, access_type: int, proxy: str, bypass: str) -> dict[str, str]:
    if access_type == 1:  # WINHTTP_ACCESS_TYPE_NO_PROXY: an explicit PAC DIRECT.
        return {}
    if access_type != 3:
        raise NetworkConfigurationError("proxy_pac", "Windows returned an unsupported proxy route.")
    host = (urlsplit(url).hostname or "").lower()
    for pattern in re.split(r"[;\s]+", bypass.lower()):
        if pattern and ((pattern == "<local>" and "." not in host) or fnmatch.fnmatchcase(host, pattern)):
            return {}
    scheme = urlsplit(url).scheme.lower()
    entries = [value.strip() for value in re.split(r"[;\s]+", proxy) if value.strip()]
    preferred = [entry.split("=", 1)[1] for entry in entries if entry.lower().startswith(scheme + "=")]
    generic = [entry for entry in entries if "=" not in entry]
    values = preferred or generic
    if not values:
        raise NetworkConfigurationError("proxy_pac", "Windows did not return a usable proxy for this URL.")
    # Respect the selected first route; do not retry a failed proxy through
    # another proxy or DIRECT, which could duplicate a paid generation request.
    selected = values[0]
    return {scheme: selected if "://" in selected else "http://" + selected}


def _windows_auto_proxy(url: str) -> dict[str, str] | None:
    """Resolve current-user PAC per URL; None means no automatic config exists.

    WinHttpGetProxyForUrl understands Windows PAC/WPAD; urllib does not. Returned
    strings are GlobalFree-owned pointers, not ctypes-managed Unicode strings.
    """
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class IEConfig(ctypes.Structure):
        _fields_ = [("auto_detect", wintypes.BOOL), ("auto_url", ctypes.c_void_p),
                    ("proxy", ctypes.c_void_p), ("bypass", ctypes.c_void_p)]

    class AutoOptions(ctypes.Structure):
        _fields_ = [("flags", wintypes.DWORD), ("detect_flags", wintypes.DWORD),
                    ("auto_url", wintypes.LPCWSTR), ("reserved", ctypes.c_void_p),
                    ("reserved_flags", wintypes.DWORD), ("auto_logon", wintypes.BOOL)]

    class ProxyInfo(ctypes.Structure):
        _fields_ = [("access_type", wintypes.DWORD), ("proxy", ctypes.c_void_p),
                    ("bypass", ctypes.c_void_p)]

    winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    winhttp.WinHttpGetIEProxyConfigForCurrentUser.argtypes = [ctypes.POINTER(IEConfig)]
    winhttp.WinHttpGetIEProxyConfigForCurrentUser.restype = wintypes.BOOL
    winhttp.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    winhttp.WinHttpOpen.restype = ctypes.c_void_p
    winhttp.WinHttpSetTimeouts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    winhttp.WinHttpSetTimeouts.restype = wintypes.BOOL
    winhttp.WinHttpGetProxyForUrl.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(AutoOptions), ctypes.POINTER(ProxyInfo)]
    winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
    winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
    winhttp.WinHttpCloseHandle.restype = wintypes.BOOL
    kernel.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel.GlobalFree.restype = ctypes.c_void_p
    config, result = IEConfig(), ProxyInfo()
    session = None
    try:
        if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
            error = ctypes.get_last_error()
            if error == 2:  # No current-user IE/Windows proxy configuration.
                return None
            raise NetworkConfigurationError("proxy_config", f"Windows proxy configuration could not be read (WinError {error}).")
        auto_url = ctypes.wstring_at(config.auto_url) if config.auto_url else ""
        if not auto_url and not config.auto_detect:
            manual = ctypes.wstring_at(config.proxy) if config.proxy else ""
            bypass = ctypes.wstring_at(config.bypass) if config.bypass else ""
            return _proxy_from_windows_result(url, 3, manual, bypass) if manual else None
        cache_key = (url, auto_url, bool(config.auto_detect))
        with _PAC_LOCK:
            cached = _PAC_CACHE.get(cache_key)
            if cached and cached[0] > time.monotonic():
                return dict(cached[1])
        session = winhttp.WinHttpOpen("PawpalCat/5 PAC", 1, None, None, 0)
        if not session:
            raise NetworkConfigurationError("proxy_pac", f"Windows proxy session could not open (WinError {ctypes.get_last_error()}).")
        if not winhttp.WinHttpSetTimeouts(session, 5000, 5000, 5000, 5000):
            raise NetworkConfigurationError("proxy_pac", f"Windows proxy timeouts could not be set (WinError {ctypes.get_last_error()}).")
        # Prefer an explicitly configured PAC URL; do not send automatic domain
        # credentials if downloading the script requires authentication.
        options = AutoOptions(2 if auto_url else 1, 0 if auto_url else 3,
                              auto_url or None, None, 0, False)
        if not winhttp.WinHttpGetProxyForUrl(session, url, ctypes.byref(options), ctypes.byref(result)):
            raise NetworkConfigurationError("proxy_pac", f"Windows PAC/WPAD resolution failed (WinError {ctypes.get_last_error()}). Check the configured automatic proxy; no direct fallback was sent.")
        proxy = ctypes.wstring_at(result.proxy) if result.proxy else ""
        bypass = ctypes.wstring_at(result.bypass) if result.bypass else ""
        route = _proxy_from_windows_result(url, result.access_type, proxy, bypass)
        with _PAC_LOCK:
            if len(_PAC_CACHE) >= 32:
                _PAC_CACHE.clear()
            _PAC_CACHE[cache_key] = (time.monotonic() + 60, dict(route))
        return route
    finally:
        for pointer in (result.proxy, result.bypass, config.auto_url, config.proxy, config.bypass):
            if pointer:
                kernel.GlobalFree(pointer)
        if session:
            winhttp.WinHttpCloseHandle(session)


def _proxy_settings(url: str) -> dict[str, str]:
    proxies = dict(getproxies())
    scheme = urlsplit(url).scheme.lower()
    host = urlsplit(url).netloc
    if host and proxy_bypass(host):
        # The configured no_proxy/Windows bypass list explicitly permits direct.
        return {}
    selected = proxies.get(scheme) or proxies.get("all")
    if not selected:
        resolved = _windows_auto_proxy(url)
        if resolved is None:
            return proxies
        if not resolved:
            return {}
        proxies = resolved
        selected = resolved.get(scheme)
    value = str(selected).strip()
    try:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        if not parsed.hostname:
            raise ValueError("Missing proxy host")
        _ = parsed.port
    except ValueError as exc:
        raise NetworkConfigurationError("proxy_invalid", "The configured HTTP/HTTPS proxy address or port is invalid.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise NetworkConfigurationError("proxy_unsupported", "The configured proxy uses SOCKS or an unsupported protocol. Set HTTP_PROXY/HTTPS_PROXY to the proxy's HTTP or mixed port. The application did not bypass it.")
    proxies[scheme] = value if "://" in value else "http://" + value
    return proxies


def build_network_opener(*handlers, url: str):
    proxies = _proxy_settings(url)
    context = ssl.create_default_context()
    # Normal certificate/hostname validation and Windows trusted roots stay on.
    return build_opener(ProxyHandler(proxies), HTTPSHandler(context=context), *handlers)


def open_url(request, timeout: float = 15):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    return build_network_opener(url=url).open(request, timeout=timeout)


def network_failure(exc: BaseException, secret: str = "") -> tuple[str, str]:
    """Return a stable category and bounded, sanitized actionable detail."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, NetworkConfigurationError):
        return reason.kind, safe_detail(reason, secret)
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
        return "timeout", "Connection timed out. Check the configured proxy and service availability."
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "tls", "TLS certificate verification failed. Check the system clock and trusted certificates/proxy certificate. Certificate verification was kept enabled."
    if isinstance(reason, ssl.SSLError):
        return "tls", "TLS negotiation failed. Check HTTPS/proxy compatibility and trusted certificates."
    if isinstance(reason, socket.gaierror):
        return "dns", "DNS lookup failed. Check network and DNS/proxy settings."
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused", "Connection refused. Check that the service or configured proxy is running and its port is correct."
    detail = safe_detail(reason, secret)
    return "network", detail or "Network connection failed. Check the service and configured proxy."
