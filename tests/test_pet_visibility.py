"""Hide/show stays reversible, stops voice, and preserves animation clocks."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_cat_app import APP_MODULE
from pet_behavior import PetBehaviorManager
from pet_motion import ClipSpec, MotionPlayer
from pet_i18n import localize


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        self.app = app
        app.pet_hidden = False
        app.language = "zh-CN"
        app.settings = {"show_temperature": False}
        app.root = Mock()
        app.tray_icon = Mock()
        app._close_context_menu = Mock()
        app._cancel_voice_jobs = Mock()
        app.quick_voice = Mock()
        app.bubble = Mock(alpha=1)
        app._update_tray_metrics = Mock()
        app.history = [{"role": "user", "content": "保留上下文"}]
        app.behavior = PetBehaviorManager(state_duration={"eat": 8})
        app.behavior.manual_action("eat", now=100)
        app.motion = MotionPlayer({"idle": ClipSpec(10, 20, (0, 20)),
                                    "eat": ClipSpec(10, 80, (0, 80), loop=False)}, now=100)
        app.motion_pose = app.motion.update("eat", 102, started_at=100)
        app._animation_job = "existing-timer"

    def hide(self):
        with patch.object(APP_MODULE.time, "monotonic", return_value=102):
            self.app._hide_pet()

    def test_hide_stops_voice_and_overlays_but_keeps_tray_and_context(self):
        self.hide()
        app = self.app
        self.assertTrue(app.pet_hidden)
        app._cancel_voice_jobs.assert_called_once()
        app.quick_voice.hide.assert_called_once()
        app.root.withdraw.assert_called_once()
        app.tray_icon.stop.assert_not_called()
        self.assertEqual(app.bubble.alpha, 0)
        self.assertFalse(app.quick_voice.active)
        self.assertEqual(app.history[0]["content"], "保留上下文")

    def test_hide_is_idempotent_and_disabled_without_tray(self):
        self.app.tray_icon = None
        self.hide()
        self.app.root.withdraw.assert_not_called()
        self.assertFalse(self.app.pet_hidden)
        self.app.tray_icon = Mock()
        self.hide()
        self.hide()
        self.app._cancel_voice_jobs.assert_called_once()

    def test_hidden_loop_does_not_decode_move_or_draw_and_keeps_tray_sampling(self):
        app = self.app
        self.hide()
        app._track_hover_pointer = Mock()
        app._sample_cpu = Mock()
        app._maybe_refresh_weather = Mock()
        app._draw = Mock()
        app.motion.update = Mock()
        app._animate()
        app._draw.assert_not_called()
        app._track_hover_pointer.assert_not_called()
        app.motion.update.assert_not_called()
        app._sample_cpu.assert_called_once()
        app._maybe_refresh_weather.assert_called_once()
        app.root.after.assert_called_once_with(1000, app._animate)

    def test_show_resumes_same_frame_and_does_not_duplicate_animation_loop(self):
        app = self.app
        before = app.motion_pose
        self.hide()
        with patch.object(APP_MODULE.time, "monotonic", return_value=162):
            app._show_pet()
            app._show_pet()
        after = app.motion.update(app.behavior.state, 162, started_at=app.behavior.state_started)
        self.assertEqual(before, after)
        self.assertEqual(app.behavior.state_until, 168)
        self.assertFalse(app.pet_hidden)
        app.root.deiconify.assert_called_once()
        app.root.after_cancel.assert_called_once_with("existing-timer")
        app.root.after.assert_called_once_with(0, app._animate)

    def test_sleep_remains_asleep_after_hidden_interval(self):
        app = self.app
        app.behavior.manual_action("idle", now=100)
        app.behavior.manual_action("sleep", now=100)
        self.hide()
        with patch.object(APP_MODULE.time, "monotonic", return_value=300):
            app._show_pet()
        self.assertEqual(app.behavior.state, "sleep")
        self.assertIsNone(app.behavior.state_until)

    def test_hidden_tray_default_restores_pet_and_keeps_manual_chat(self):
        app = self.app
        app.pet_hidden = True
        app._schedule_ui = lambda fn: fn()
        app._show_pet = Mock()
        menu = app._tray_menu()
        default = next(item for item in menu.items if item.default)
        self.assertEqual(default.text, "显示猫咪")
        default(None)
        app._show_pet.assert_called_once()
        self.assertIn("和猫咪聊天", [item.text for item in menu.items])

    def test_hide_and_show_labels_support_existing_languages(self):
        for language in ("en", "ms"):
            for label in ("隐藏猫咪", "显示猫咪"):
                self.assertNotEqual(localize(label, language), label)


if __name__ == "__main__":
    unittest.main()
