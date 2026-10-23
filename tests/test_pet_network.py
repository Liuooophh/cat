from __future__ import annotations

import ctypes
import socket
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

import pet_network as network


class NetworkTests(unittest.TestCase):
    def setUp(self):
        network._PAC_CACHE.clear()
        self.bypass = patch("pet_network.proxy_bypass", return_value=False)
        self.bypass.start()
        self.addCleanup(self.bypass.stop)

    def test_all_proxy_is_honored_and_unsupported_socks_never_bypassed(self):
        with patch("pet_network.getproxies", return_value={"all": "socks5://user:SECRET@proxy.test:1080"}), patch("pet_network.build_opener") as opener:
            with self.assertRaises(network.NetworkConfigurationError) as caught:
                network.build_network_opener(url="https://api.deepseek.com/models")
        self.assertEqual(caught.exception.kind, "proxy_unsupported")
        self.assertNotIn("SECRET", str(caught.exception))
        opener.assert_not_called()
        with patch("pet_network.getproxies", return_value={"all": "http://proxy.test:8080"}):
            self.assertEqual(network._proxy_settings("https://service.test"),
                             {"all": "http://proxy.test:8080", "https": "http://proxy.test:8080"})

    def test_pac_route_direct_proxy_and_failure_do_not_fallback(self):
        with patch("pet_network.getproxies", return_value={}):
            with patch("pet_network._windows_auto_proxy", return_value={}):
                self.assertEqual(network._proxy_settings("https://service.test"), {})
            with patch("pet_network._windows_auto_proxy", return_value={"https": "http://proxy.test:80"}):
                self.assertEqual(network._proxy_settings("https://service.test"), {"https": "http://proxy.test:80"})
            with patch("pet_network._windows_auto_proxy", side_effect=network.NetworkConfigurationError("proxy_pac", "PAC failed")), patch("pet_network.build_opener") as opener:
                with self.assertRaises(network.NetworkConfigurationError):
                    network.build_network_opener(url="https://service.test")
                opener.assert_not_called()

    def test_explicit_no_proxy_bypass_is_preserved(self):
        with patch("pet_network.getproxies", return_value={"https": "socks5://proxy.test:1080"}), patch("pet_network.proxy_bypass", return_value=True), patch("pet_network._windows_auto_proxy") as pac:
            self.assertEqual(network._proxy_settings("http://localhost:11434"), {})
        pac.assert_not_called()

    def test_windows_result_uses_matching_protocol_and_explicit_direct(self):
        self.assertEqual(network._proxy_from_windows_result("https://test", 1, "", ""), {})
        self.assertEqual(network._proxy_from_windows_result("https://test", 3, "http=a:80;https=b:81", ""), {"https": "http://b:81"})
        self.assertEqual(network._proxy_from_windows_result("https://intranet", 3, "a:80", "<local>"), {})
        with self.assertRaises(network.NetworkConfigurationError):
            network._proxy_from_windows_result("https://test", 3, "socks=a:1080", "")

    def test_tls_context_keeps_certificate_and_hostname_verification(self):
        with patch("pet_network._proxy_settings", return_value={}), patch("pet_network.build_opener") as opener:
            network.build_network_opener(url="https://test")
        handler = opener.call_args.args[1]
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)

    def test_error_details_preserve_category_and_redact_proxy_credentials(self):
        self.assertEqual(network.network_failure(URLError(socket.gaierror("fixture")))[0], "dns")
        self.assertEqual(network.network_failure(URLError(ssl.SSLCertVerificationError("fixture")))[0], "tls")
        detail = network.safe_detail("socks5://user:SECRET@proxy.test:1080 password=HIDDEN Bearer TOKEN", "TOKEN")
        for secret in ("SECRET", "HIDDEN", "TOKEN"):
            self.assertNotIn(secret, detail)

    def test_winhttp_pac_frees_all_returned_strings_and_closes_session(self):
        buffers = [ctypes.create_unicode_buffer(value) for value in (
            "https://pac.test/script", "manual:80", "*.local", "pac-proxy:8080", "<local>")]
        pointers = [ctypes.addressof(value) for value in buffers]
        def config(pointer):
            item = pointer._obj
            item.auto_detect = False
            item.auto_url, item.proxy, item.bypass = pointers[:3]
            return True
        def resolve(_handle, _url, options, pointer):
            self.assertFalse(options._obj.auto_logon)
            self.assertEqual(options._obj.flags, 2)
            item = pointer._obj
            item.access_type = 3
            item.proxy, item.bypass = pointers[3:]
            return True
        winhttp = SimpleNamespace(WinHttpGetIEProxyConfigForCurrentUser=Mock(side_effect=config),
            WinHttpOpen=Mock(return_value=123), WinHttpSetTimeouts=Mock(return_value=True),
            WinHttpGetProxyForUrl=Mock(side_effect=resolve), WinHttpCloseHandle=Mock(return_value=True))
        kernel = SimpleNamespace(GlobalFree=Mock())
        with patch("pet_network.os.name", "nt"), patch("pet_network.ctypes.WinDLL", side_effect=[winhttp, kernel], create=True):
            result = network._windows_auto_proxy("https://service.test/models")
        self.assertEqual(result, {"https": "http://pac-proxy:8080"})
        self.assertEqual(kernel.GlobalFree.call_count, 5)
        winhttp.WinHttpCloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
