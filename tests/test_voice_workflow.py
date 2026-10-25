"""Targeted regression checks for automatic voice, hover control and weather."""
import gc
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_cat_app import APP_MODULE, TextInput
from pet_cloud_voice import SpeechCredentials, voice_settings
from pet_context import WeatherLocation
from pet_settings import weather_city_name, weather_preference_update
from pet_quick_voice import QuickVoiceControl, interaction_contains
from pet_voice_catalog import VOICE_GROUPS, CHINESE_VOICES, voice_category, voice_label


class WorkflowTests(unittest.TestCase):
    def app(self):
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        app.language = "zh-CN"
        app.settings = {"tencent_speech": {}, "endpoint": "https://api.deepseek.com", "model":"deepseek-flash"}
        app.chat_input = TextInput("保留未发送的草稿")
        app.chat_text = app.chat_status = app.mic_canvas = None
        app.chat_window = None
        app._speech_input_generation = app._voice_generation = 0
        app._voice_button = app._last_reply_button = None
        app._quick_voice_mode = app._voice_reply_pending = False
        app.mic_listening = app.waiting = False
        app.quick_voice = Mock()
        app.history = [{"role":"user","content":"之前的话"}]
        app.speech_input = Mock(listening=False)
        app.speech_output = Mock()
        app._speech_credentials = Mock(return_value=SpeechCredentials("test", "test"))
        app.behavior = Mock()
        app.root = Mock()
        app.root.after.side_effect = lambda _delay, fn: fn()
        app._schedule_ui = lambda fn: fn()
        app._append_message = Mock()
        app.open_chat = Mock()
        app.api_key = "test-key"
        app._local_data_query = Mock(return_value=False)
        return app

    def test_auto_send_preserves_unrelated_draft(self):
        app = self.app(); app._send_text = Mock()
        app._finish_speech_input("  今天天气怎样  ", None, auto_send=True)
        app._send_text.assert_called_once_with("今天天气怎样", open_window=True, voice_turn=True)
        self.assertEqual(app.chat_input.text, "保留未发送的草稿")

    def test_empty_transcript_is_not_sent(self):
        app = self.app(); app._send_text = Mock()
        app._finish_speech_input("  ", None, auto_send=True, quick=True)
        app._send_text.assert_not_called()

    def test_quick_transcript_works_without_editor(self):
        app = self.app(); app.chat_input = None; app._send_text = Mock()
        app._finish_speech_input("你好", None, auto_send=True, quick=True)
        app._send_text.assert_called_once_with("你好", open_window=False, voice_turn=True)

    def test_duplicate_worker_callback_is_consumed_once(self):
        app = self.app(); app._send_text = Mock()
        app._toggle_speech_input()
        callback = app.speech_input.start.call_args.args[0]
        callback("你好", None); callback("你好", None)
        app._send_text.assert_called_once()

    def test_quick_context_shares_ai_history_without_opening_chat(self):
        app = self.app(); app.chat_input = None
        with patch.object(APP_MODULE.threading, "Thread") as thread:
            app._send_text("现在的话", open_window=False, voice_turn=True)
        app.open_chat.assert_not_called()
        sent = thread.call_args.kwargs["args"][3]
        self.assertEqual([item["content"] for item in sent[1:]], ["之前的话", "现在的话"])
        self.assertTrue(app.waiting)

    def test_voice_response_plays_even_when_normal_auto_read_is_off(self):
        app = self.app(); app._voice_reply_pending = app._quick_voice_mode = True
        app._play_voice_reply = Mock()
        app._finish_ai("语音回答", None)
        app._play_voice_reply.assert_called_once_with("语音回答", "zh-CN", None)
        self.assertEqual(app.history[-1], {"role":"assistant","content":"语音回答"})

    def test_cancel_while_thinking_prevents_late_auto_audio(self):
        app = self.app(); app.settings["tencent_speech"] = {"auto_play":True}
        app.waiting = app._voice_reply_pending = app._quick_voice_mode = True
        app.chat_text = Mock(); app._play_voice_reply = Mock()
        app._cancel_quick_voice()
        app._finish_ai("迟来的回答", None)
        app._play_voice_reply.assert_not_called()

    def test_busy_ai_does_not_start_another_recording(self):
        app = self.app(); app.waiting = True
        app._toggle_speech_input(quick=True)
        app.speech_input.start.assert_not_called()

    def test_voice_config_can_disable_automatic_submission(self):
        self.assertTrue(voice_settings()["auto_send"])
        self.assertFalse(voice_settings({"auto_send":False})["auto_send"])


class WeatherPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.location = WeatherLocation(1,"青岛",36.07,120.38,"中国","山东","Asia/Shanghai")
        self.settings = {"weather_city":"青岛","weather_location":self.location.to_dict()}

    def test_untouched_weather_is_not_overwritten_even_if_editor_is_empty(self):
        self.assertEqual(weather_preference_update(self.settings, "", None, False), {})

    def test_legacy_coordinates_restore_city_name(self):
        self.assertEqual(weather_city_name({"weather_location":self.location.to_dict()}), "青岛")

    def test_explicit_clear_and_invalid_edit_are_distinct(self):
        self.assertEqual(weather_preference_update(self.settings, "", None, True), {"weather_city":"","weather_location":None})
        with self.assertRaises(ValueError):
            weather_preference_update(self.settings, "上海", None, True)

    def test_saved_location_survives_other_settings_and_restart(self):
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        with tempfile.TemporaryDirectory() as directory:
            app.settings_path = Path(directory)/"settings.json"
            app.settings = {**self.settings,"show_fps":False}
            app.settings.update(weather_preference_update(app.settings,"",None,False))
            app._save_settings_file()
            loaded = app._load_settings()
        self.assertEqual(loaded["weather_city"], "青岛")
        self.assertEqual(loaded["weather_location"], self.location.to_dict())
        self.assertFalse(loaded["show_fps"])

    def test_real_tk_city_variable_survives_gc_and_saving_other_options(self):
        import tkinter as tk
        from tkinter import ttk
        import pet_settings
        root = tk.Tk(); root.withdraw()
        self.addCleanup(root.destroy)
        win = tk.Toplevel(root); win.withdraw(); win.close_window = Mock()
        pet_settings._settings_style(win)
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        app.root = root; app.language = "zh-CN"
        app.settings = dict(self.settings)
        app.startup_status = None; app.startup_error = None
        app.metrics = Mock(); app.context_reactions = Mock()
        app._update_chat_context_status = Mock()
        app._save_settings_file = Mock()
        with patch.object(pet_settings,"_window",return_value=win):
            pet_settings.open_preferences(app)
        gc.collect(); root.update_idletasks()
        self.assertEqual(win._weather_query_var.get(), "青岛")
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        save = next(widget for widget in descendants(win)
                    if isinstance(widget,ttk.Button) and widget.cget("text")=="保存设置")
        save.invoke()
        self.assertEqual(app.settings["weather_city"], "青岛")
        self.assertEqual(app.settings["weather_location"],self.location.to_dict())
        app._save_settings_file.assert_called_once()


class HoverAndCatalogTests(unittest.TestCase):
    def test_real_voice_settings_category_preview_and_save_use_selected_chinese_voice(self):
        import tkinter as tk
        from tkinter import ttk
        import pet_settings
        import pet_voice_settings
        root = tk.Tk(); root.withdraw()
        self.addCleanup(root.destroy)
        win = tk.Toplevel(root); win.withdraw(); win.close_window = Mock()
        pet_settings._settings_style(win)
        app = SimpleNamespace(root=root,language="en",settings={"tencent_speech":{"voice_zh":603004}},
                              _load_api_key=Mock(return_value=""),_store_api_key=Mock(),
                              _save_settings_file=Mock(),_cancel_voice_jobs=Mock(), speech_output=Mock())
        app._t = lambda text: APP_MODULE.localize(text, app.language)
        with patch.object(pet_voice_settings,"_window",return_value=win):
            pet_voice_settings.open_speech_settings(app)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(win))
        box = next(w for w in widgets if isinstance(w,ttk.Combobox) and "603004" in w.get())
        self.assertIn("超自然大模型音色",box.get())
        preview = next(w for w in widgets if isinstance(w,ttk.Button) and "Preview" in w.cget("text"))
        preview.invoke()
        args = app.speech_output.play.call_args.args
        self.assertEqual(args[1],"zh-CN")
        self.assertEqual(args[3]["voice_zh"],603004)
        save = next(w for w in widgets if isinstance(w,ttk.Button) and w.cget("text")==app._t("保存设置"))
        save.invoke()
        self.assertEqual(app.settings["tencent_speech"]["voice_zh"],603004)
        app._save_settings_file.assert_called_once()

    def test_gap_between_pet_and_microphone_remains_interactive(self):
        pet, control = (100,100,200,300), (220,170,264,214)
        for point in ((190,180),(210,180),(240,180)):
            self.assertTrue(interaction_contains(point,pet,control))
        self.assertFalse(interaction_contains((400,400),pet,control))

    def test_active_control_remains_visible_outside_hover_region(self):
        app = SimpleNamespace(pet_scale=1,current_sprite_bbox=(10,10,110,190),language="zh-CN",root=Mock())
        app.root.winfo_rootx.return_value = app.root.winfo_rooty.return_value = 100
        app.root.winfo_screenwidth.return_value=800; app.root.winfo_screenheight.return_value=600
        control = QuickVoiceControl(app)
        control.window=Mock();control.surface=Mock()
        control.set_status("正在聆听")
        control.tick(700,500)
        self.assertTrue(control.visible)

    def test_all_chinese_voices_round_trip_and_keep_original_choices(self):
        ids = [number for number,_name in CHINESE_VOICES]
        self.assertEqual(len(ids), len(set(ids)))
        for number in ids:
            self.assertEqual(voice_settings({"voice_zh":number})["voice_zh"], number)
            self.assertIn(voice_category(number), VOICE_GROUPS)
            self.assertIn(str(number), voice_label(number))
        self.assertTrue({101001,101015,402001}.issubset(ids))


if __name__ == "__main__":
    unittest.main()
