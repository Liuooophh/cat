"""First hover lifecycle and real PCM feedback; no live audio or cloud calls."""
import ctypes
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pet_quick_voice import QuickVoiceControl, HEIGHT, render_control, render_feedback
from test_cat_app import APP_MODULE


class FirstHoverTests(unittest.TestCase):
    def setUp(self):
        root = Mock()
        root.winfo_rootx.return_value = root.winfo_rooty.return_value = 100
        root.winfo_screenwidth.return_value = 800
        root.winfo_screenheight.return_value = 600
        root.winfo_pointerxy.return_value = (140, 140)
        root.after.return_value = "poll-job"
        self.app = SimpleNamespace(root=root, pet_scale=1, language="zh-CN",
                                   current_sprite_bbox=(10, 10, 110, 190))
        self.control = QuickVoiceControl(self.app)

    def create(self):
        self.control.window = Mock()
        self.control.surface = Mock()

    def test_first_scheduled_poll_reveals_without_an_animation_tick(self):
        with patch.object(self.control, "_create", side_effect=self.create) as create:
            self.control.start()
            self.control.start()
            self.app.root.after.assert_called_once_with(0, self.control._poll)
            self.control._poll()
        create.assert_called_once()
        self.assertTrue(self.control.visible)
        self.control.surface.present.assert_called_once()

    def test_first_show_maps_before_submitting_visible_pixels(self):
        self.create()
        events = []
        self.control.window.deiconify.side_effect = lambda: events.append("map")
        self.control.surface.present.side_effect = lambda _frame: events.append("present")
        self.control.tick(140, 140)
        self.assertEqual(events, ["map", "present"])

    def test_map_event_invalidates_cache_for_a_fresh_frame(self):
        self.create()
        self.control._signature = ("cached",)
        self.control._next_tick = float("inf")
        self.control._mapped(SimpleNamespace(widget=self.control.window))
        self.control.tick(140, 140)
        self.assertTrue(self.control.visible)
        self.control.surface.present.assert_called_once()

    def test_transient_creation_failure_retries_without_restart_or_busy_loop(self):
        with patch.object(self.control, "_create", side_effect=[OSError("temporarily unavailable"), None]) as create:
            with patch("pet_quick_voice.time.monotonic", return_value=100), self.assertLogs("pet_quick_voice", level="WARNING"):
                self.control.tick(140, 140)
            with patch("pet_quick_voice.time.monotonic", return_value=100.5):
                self.control.tick(140, 140)
            self.assertEqual(create.call_count, 1)
            create.side_effect = self.create
            with patch("pet_quick_voice.time.monotonic", return_value=101.1):
                self.control.tick(140, 140)
            self.assertEqual(create.call_count, 2)
        self.assertTrue(self.control.visible)

    def test_close_cancels_poll_and_cannot_restart_it(self):
        self.control.start()
        self.control.close()
        self.app.root.after_cancel.assert_called_once_with("poll-job")
        self.app.root.after.reset_mock()
        self.control.start()
        self.control._poll()
        self.app.root.after.assert_not_called()


class FeedbackTests(unittest.TestCase):
    def test_audio_amplitude_changes_bars_without_modifying_cached_artwork(self):
        base = render_control(300, "正在聆听", True, False, True)
        saved = base.tobytes()
        quiet = render_feedback(base, "voice.recording", 0, 0, True)
        loud = render_feedback(base, "voice.recording", 1, 0, True)
        self.assertNotEqual(quiet.tobytes(), loud.tobytes())
        self.assertEqual(base.tobytes(), saved)
        self.assertEqual(base.crop((76, 0, 300, HEIGHT)).tobytes(),
                         loud.crop((76, 0, 300, HEIGHT)).tobytes())

    def test_thinking_playing_and_quiet_listening_have_visible_motion(self):
        base = render_control(HEIGHT, "", True, False, True)
        for phase in ("voice.recording", "voice.transcribing", "voice.thinking",
                      "voice.synthesizing", "voice.playing"):
            with self.subTest(phase=phase):
                first = render_feedback(base, phase, 0, 0, True)
                next_frame = render_feedback(base, phase, 0, 3, True)
                self.assertNotEqual(first.tobytes(), next_frame.tobytes())
                self.assertEqual(first.getpixel((0, 0))[3], 0)

    def test_feedback_follows_left_hand_button(self):
        base = render_control(300, "正在聆听", True, False, False)
        frame = render_feedback(base, "voice.recording", 1, 0, False)
        self.assertEqual(base.crop((0, 0, 224, HEIGHT)).tobytes(),
                         frame.crop((0, 0, 224, HEIGHT)).tobytes())

    def test_status_key_is_passed_to_control_and_terminal_ends_feedback(self):
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        app.language, app._quick_voice_mode = "zh-CN", True
        app.chat_status, app.quick_voice = None, Mock()
        app._voice_status("voice.playing")
        self.assertEqual(app.quick_voice.set_status.call_args.kwargs["phase"], "voice.playing")
        control = QuickVoiceControl(SimpleNamespace())
        control.set_status("正在聆听", phase="voice.recording")
        control._meter = .8
        control.set_status("已取消", terminal=True)
        self.assertEqual(control._meter, 0)
        self.assertFalse(control.active)
        self.assertEqual(control.phase, "")

    def test_pcm_level_reaches_visual_control_and_clears_in_recognition_phase(self):
        root = Mock()
        root.winfo_rootx.return_value = root.winfo_rooty.return_value = 100
        root.winfo_screenwidth.return_value, root.winfo_screenheight.return_value = 800, 600
        app = SimpleNamespace(root=root, pet_scale=1, current_sprite_bbox=(10, 10, 110, 190),
                              speech_input=SimpleNamespace(input_level=1))
        control = QuickVoiceControl(app)
        control.window, control.surface = Mock(), Mock()
        control.set_status("正在聆听", phase="voice.recording")
        control.tick(140, 140)
        self.assertGreater(control._meter, 0)
        control.set_status("正在识别", phase="voice.transcribing")
        control.tick(140, 140)
        self.assertEqual(control._meter, 0)


class NativeFirstMapTests(unittest.TestCase):
    def test_first_hover_maps_a_native_window_with_visible_alpha_pixels(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        # A real mapped window is needed to exercise the lifecycle. Keep both
        # windows far outside the desktop; never move the user's mouse.
        x, y = root.winfo_screenwidth()+10000, root.winfo_screenheight()+10000
        root.geometry(f"150x210+{x}+{y}")
        root.overrideredirect(True)
        root.update()
        app = SimpleNamespace(root=root, pet_scale=1, current_sprite_bbox=(10, 10, 110, 190))
        control = QuickVoiceControl(app)
        self.addCleanup(control.close)
        with patch.object(root, "winfo_pointerxy", return_value=(x+40, y+40)):
            control.start()
            root.update()
        self.assertTrue(control.visible)
        self.assertEqual(control.window.state(), "normal")
        data = ctypes.string_at(control.surface._dib_bits, HEIGHT*HEIGHT*4)
        self.assertGreater(max(data[3::4]), 0)
        control.hide()
        control._next_tick = 0
        control.tick(x+40, y+40)
        self.assertTrue(control.visible)


if __name__ == "__main__":
    unittest.main()
