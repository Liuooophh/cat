from __future__ import annotations

import unittest
from string import Formatter

from pet_i18n import CATALOG, LANGUAGES, localize, normalize_language, tr


class LanguageTests(unittest.TestCase):
    def test_supported_language_aliases(self):
        for value, expected in (("en-US", "en"), ("ms_MY", "ms"), ("简体中文", "zh-CN"), ("Bahasa Melayu", "ms"), (None, "zh-CN"), ("unsupported", "zh-CN")):
            self.assertEqual(normalize_language(value), expected)

    def test_every_entry_has_three_matching_templates(self):
        for key, variants in CATALOG.items():
            self.assertEqual(len(variants), 3, key)
            fields = [{field for _, field, _, _ in Formatter().parse(text) if field} for text in variants]
            self.assertEqual(fields[0], fields[1], key)
            self.assertEqual(fields[0], fields[2], key)
            self.assertTrue(all(variants), key)

    def test_switches_static_ui_text_in_both_directions(self):
        for key in ("chat", "actions", "click", "eat", "sleep", "language", "startup", "ai_title", "cancel", "weather.clear", "fps.permission_denied"):
            for source in LANGUAGES:
                for target in LANGUAGES:
                    self.assertEqual(localize(tr(key, source), target), tr(key, target), (key, source, target))

    def test_formatted_ui_text_can_switch_language(self):
        for key, args in (("need_key", {"provider": "DeepSeek"}), ("weather_loading", {"city": "Kuala Lumpur"}), ("updated_at", {"time": "21:43"}), ("size_save_failed", {"detail": "disk full"})):
            for source in LANGUAGES:
                for target in LANGUAGES:
                    self.assertEqual(localize(tr(key, source, **args), target), tr(key, target, **args))

    def test_unknown_content_unchanged(self):
        for language in LANGUAGES:
            self.assertEqual(localize("用户刚刚说的自由文本 hello", language), "用户刚刚说的自由文本 hello")
        self.assertEqual(tr("missing.key", "en"), "missing.key")


if __name__ == "__main__":
    unittest.main()
