from __future__ import annotations

import json
import io
import ssl
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from pet_context import WeatherError, WeatherLocation, fetch_weather, fetch_weather_at, search_weather_locations


class Response:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit: int = -1) -> bytes:
        return json.dumps(self.value).encode("utf-8")


class WeatherTests(unittest.TestCase):
    def test_search_returns_disambiguated_locations_and_language(self) -> None:
        payload = {"results": [
            {"id": 1, "name": "Springfield", "latitude": 1, "longitude": 2, "admin1": "Illinois", "country": "United States"},
            {"id": 2, "name": "Springfield", "latitude": 3, "longitude": 4, "admin1": "Missouri", "country": "United States"}]}
        with patch("pet_context.urlopen", return_value=Response(payload)) as opener:
            locations = search_weather_locations("Springfield", "en")
        self.assertEqual(len(locations), 2)
        self.assertNotEqual(locations[0].label, locations[1].label)
        query = parse_qs(urlparse(opener.call_args.args[0].full_url).query)
        self.assertEqual(query["count"], ["10"])
        self.assertEqual(query["language"], ["en"])

    def test_saved_coordinates_fetch_directly_with_timestamp(self) -> None:
        location = WeatherLocation(12, "Kuala Lumpur", 3.14, 101.69, "Malaysia", "Kuala Lumpur", "Asia/Kuala_Lumpur")
        payload = {"current": {"temperature_2m": 28.5, "weather_code": 3, "time": "2026-10-23T21:45"}, "timezone": "Asia/Kuala_Lumpur"}
        with patch("pet_context.urlopen", return_value=Response(payload)) as opener:
            reading = fetch_weather_at(location.to_dict(), "ms")
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(reading.condition, "Mendung")
        self.assertEqual(reading.observed_at, "2026-10-23T21:45")
        self.assertEqual(reading.timezone, location.timezone)
        self.assertGreater(reading.fetched_at, 0)
        self.assertEqual(reading.source, "Open-Meteo")
        query = parse_qs(urlparse(opener.call_args.args[0].full_url).query)
        self.assertEqual(query["latitude"], ["3.14"])

    def test_coordinate_round_trip_and_no_duplicate_label(self) -> None:
        location = WeatherLocation(None, "Singapore", 1.35, 103.82, "Singapore")
        self.assertEqual(location.label, "Singapore")
        self.assertEqual(WeatherLocation.from_dict(location.to_dict()), location)

    def test_no_results_does_not_silently_select_anything(self) -> None:
        with patch("pet_context.urlopen", return_value=Response({})):
            self.assertEqual(search_weather_locations("no-such-city"), [])
            with self.assertRaises(WeatherError) as caught:
                fetch_weather("no-such-city", "en")
        self.assertEqual(caught.exception.code, "location_not_found")
        self.assertIn("city", str(caught.exception))

    def test_invalid_coordinates_rejected_before_network(self) -> None:
        with patch("pet_context.urlopen") as opener:
            for latitude in (91, float("nan"), float("inf")):
                with self.assertRaises(WeatherError):
                    fetch_weather_at({"name": "x", "latitude": latitude, "longitude": 0})
        opener.assert_not_called()

    def test_network_failure_has_localized_error_and_code(self) -> None:
        with patch("pet_context.urlopen", side_effect=URLError("connection refused")):
            with self.assertRaises(WeatherError) as caught:
                search_weather_locations("KL", "ms")
        self.assertEqual(caught.exception.code, "network_error")
        self.assertIn("cuaca", str(caught.exception))

    def test_timeout_is_distinct_from_network_error(self) -> None:
        with patch("pet_context.urlopen", side_effect=URLError(TimeoutError("timed out"))):
            with self.assertRaises(WeatherError) as caught:
                search_weather_locations("KL", "en")
        self.assertEqual(caught.exception.code, "timeout")

    def test_invalid_forecast_rejected(self) -> None:
        location = WeatherLocation(None, "KL", 3, 101)
        for payload in ({}, {"current": {"temperature_2m": float("nan"), "weather_code": 0}}, {"current": {"temperature_2m": 26}}):
            with patch("pet_context.urlopen", return_value=Response(payload)):
                with self.assertRaises(WeatherError) as caught:
                    fetch_weather_at(location)
            self.assertEqual(caught.exception.code, "invalid_response")

    def test_legacy_city_lookup_still_works(self) -> None:
        responses = [Response({"results": [{"name": "上海", "latitude": 31.23, "longitude": 121.47}]}),
                     Response({"current": {"temperature_2m": 30.4, "weather_code": 61}})]
        with patch("pet_context.urlopen", side_effect=responses):
            reading = fetch_weather("上海")
        self.assertEqual(reading.condition, "小雨")
        self.assertTrue(reading.notable)

    def test_service_http_status_and_safe_reason_are_preserved(self):
        error = HTTPError("https://test", 429, "limit", {},
                          io.BytesIO(b'{"reason":"request limit reached"}'))
        with patch("pet_context.urlopen", side_effect=error), self.assertRaises(WeatherError) as caught:
            search_weather_locations("KL", "en")
        self.assertEqual(caught.exception.status, 429)
        self.assertIn("429", str(caught.exception))
        self.assertIn("request limit", caught.exception.detail)

    def test_tls_and_proxy_failures_are_actionable(self):
        from pet_network import NetworkConfigurationError
        for error, expected in ((URLError(ssl.SSLCertVerificationError("fixture")), "certificate"),
                                (NetworkConfigurationError("proxy_unsupported", "SOCKS proxy fixture"), "SOCKS")):
            with patch("pet_context.urlopen", side_effect=error), self.assertRaises(WeatherError) as caught:
                search_weather_locations("KL", "en")
            self.assertEqual(caught.exception.code, "network_error")
            self.assertIn(expected, caught.exception.detail)

    def test_invalid_service_body_reason_is_not_silently_discarded(self):
        with patch("pet_context.urlopen", return_value=Response({"error": True, "reason": "Invalid coordinates fixture"})):
            with self.assertRaises(WeatherError) as caught:
                fetch_weather_at(WeatherLocation(None, "KL", 3, 101))
        self.assertIn("Invalid coordinates", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
