"""App integration checks that do not start desktop windows or use final media."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

SOURCE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_DIR))

from pet_behavior import PetBehaviorManager
from pet_motion import MotionPlayer
from pet_metrics import MetricsSnapshot
from pet_presentation import AnimationController, SpeechBubble

APP_LOADER = SourceFileLoader("cat_app_integration_module", str(SOURCE_DIR / "desktop_ai_pet.pyw"))
APP_MODULE = module_from_spec(spec_from_loader(APP_LOADER.name, APP_LOADER))
APP_LOADER.exec_module(APP_MODULE)


class TextInput:
    def __init__(self, text: str) -> None:
        self.text = text
        self.focus_set = Mock()
        self.configure = Mock()

    def get(self, _start: str, _end: str) -> str:
        return self.text

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, position: str, text: str) -> None:
        self.text = text + self.text if position == "1.0" else self.text + text


class CatAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)
        self.pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        self.pet.language = "zh-CN"
        self.pet.pending_action = None
        self.pet.chat_window = None
        self.pet.context_window = None
        self.pet.chat_status = None
        self.pet.chat_context_status = None
        self.pet.weather_info = None
        self.pet.weather_error = None
        self.pet.cpu_percent = None
        self.pet.metrics_snapshot = MetricsSnapshot()
        self.pet.metrics = Mock()
        self.pet.api_key = ""
        self.pet.connection_verified = False

    def test_new_and_migrated_profiles_default_to_quiet_without_losing_ai(self) -> None:
        self.pet.settings_path = self.directory_path / "settings.json"
        defaults = self.pet._load_settings()
        for setting in ("cpu_reactions", "night_reactions"):
            self.assertFalse(defaults[setting])
        self.assertTrue(defaults["show_status_bubble"])
        self.assertTrue(defaults["start_with_windows"])
        self.assertEqual(defaults["cat_settings_version"], 3)
        self.assertNotIn("voice_reply_enabled", defaults)
        self.pet.settings_path.write_text(json.dumps({
            "endpoint": "http://localhost:11434/v1/chat/completions",
            "model": "local-model", "weather_city": "上海", "pet_scale": 1.2,
            "cpu_reactions": True, "night_reactions": True, "voice_reply_enabled": True,
        }), encoding="utf-8")
        migrated = self.pet._load_settings()
        self.assertEqual(migrated["model"], "local-model")
        self.assertEqual(migrated["weather_city"], "上海")
        self.assertEqual(migrated["pet_scale"], 1.2)
        for setting in ("cpu_reactions", "night_reactions"):
            self.assertFalse(migrated[setting])
        self.assertNotIn("voice_reply_enabled", migrated)
        self.assertTrue(migrated["show_status_bubble"])
        self.pet.settings_path.write_text(json.dumps({
            **migrated, "voice_reply_enabled": True, "show_status_bubble": False,
            "language": "ms", "start_with_windows": False,
        }), encoding="utf-8")
        reloaded = self.pet._load_settings()
        self.assertNotIn("voice_reply_enabled", reloaded)
        self.assertFalse(reloaded["show_status_bubble"])
        self.assertFalse(reloaded["start_with_windows"])
        self.assertEqual(reloaded["language"], "ms")

    def test_speech_transcription_only_fills_input_even_when_empty(self) -> None:
        for existing, expected in (("说点什么…", "你好"), ("今天", "今天 你好")):
            with self.subTest(existing=existing):
                self.pet.root = Mock()
                self.pet.chat_input = TextInput(existing)
                self.pet.chat_status = Mock()
                self.pet.mic_canvas = Mock()
                self.pet.mic_listening = True
                self.pet.waiting = False
                self.pet._send_from_input = Mock()
                self.pet._send_text = Mock()
                self.pet._finish_speech_input("你好", None)
                self.assertEqual(self.pet.chat_input.text, expected)
                self.assertFalse(self.pet.mic_listening)
                self.pet._send_from_input.assert_not_called()
                self.pet._send_text.assert_not_called()
                self.assertEqual(self.pet.root.after.call_args.args[0], 5000)

    def test_retired_deepseek_model_settings_are_migrated_without_losing_endpoint(self) -> None:
        self.pet.settings_path = self.directory_path / "settings.json"
        self.pet.settings_path.write_text(json.dumps({"endpoint": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "ai_profiles": {"DeepSeek": {"endpoint": "https://api.deepseek.com", "model": "deepseek-reasoner"}}}), encoding="utf-8")
        settings = self.pet._load_settings()
        self.assertEqual(settings["endpoint"], "https://api.deepseek.com/v1")
        self.assertEqual(settings["model"], "deepseek-flash")
        self.assertEqual(settings["ai_profiles"]["DeepSeek"]["model"], "deepseek-flash")

    def test_settings_write_failure_preserves_previous_json(self) -> None:
        self.pet.settings_path = self.directory_path / "settings.json"
        self.pet.settings_path.write_text('{"previous":true}', encoding="utf-8")
        self.pet.settings = {"new": True}
        with patch.object(APP_MODULE.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.pet._save_settings_file()
        self.assertEqual(json.loads(self.pet.settings_path.read_text(encoding="utf-8")), {"previous": True})
        self.assertEqual(list(self.directory_path.glob("settings-*.tmp")), [])

    def test_sending_chat_keeps_sleeping_cat_asleep_and_local_reply_async(self) -> None:
        self.pet.root = Mock()
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.manual_action("sleep")
        self.pet.open_chat = Mock()
        self.pet._append_message = Mock()
        self.pet.settings = {}
        self.pet.history = []
        self.pet.waiting = False
        self.pet._finish_local_reply = Mock()
        self.pet._send_text("你好")
        self.assertEqual(self.pet.behavior.state, "sleep")
        self.assertIsNone(self.pet.behavior.state_until)
        self.assertEqual(self.pet.history, [{"role": "user", "content": "你好"}])
        self.pet._append_message.assert_called_once_with("user", "你好")
        self.pet._finish_local_reply.assert_not_called()
        self.assertEqual(self.pet.root.after.call_args.args[0], 450)

    def _prepare_drawing(self, *, facing: str = "left") -> None:
        assets = self.directory_path / "cat"
        assets.mkdir(exist_ok=True)
        for state in ("idle", "walk", "groom", "sleep"):
            (assets / state).mkdir(exist_ok=True)
            sprite = Image.new("RGBA", (320, 300))
            sprite.putpixel((10, 282), (90, 80, 70, 255))
            sprite.save(assets / state / "0001.png")
        (assets / "clips.json").write_text(json.dumps({
            "cell_size": [320, 300], "ground_y": 282,
            "clips": {state: {"fps": 24, "loop": True, "source_facing": facing}
                      for state in ("idle", "walk", "groom", "sleep")},
        }), encoding="utf-8")
        self.pet.animation = AnimationController(assets)
        self.pet.motion = MotionPlayer(self.pet.animation.motion_specs, 1.0)
        self.pet.motion_pose = self.pet.motion.update("walk", 1.0, started_at=1.0)
        self.pet.width, self.pet.height = 360, 430
        self.pet.window_width, self.pet.window_height = 360, 430
        self.pet.behavior = SimpleNamespace(state="walk", state_started=1.0)
        self.pet._visual_state = "walk"
        self.pet._actor_mirror = False
        self.pet._visual_mirror = False
        self.pet._visual_started = 1.0
        self.pet._last_sprite = None
        self.pet._transition_sprite = None
        self.pet._transition_started = 0.0
        self.pet.bubble = SpeechBubble()
        self.pet.context_bubble_active = False
        self.pet._present_layered_frame = Mock()

    def test_walk_mirroring_respects_source_direction_and_canvas_ground(self) -> None:
        self._prepare_drawing()
        for direction, expected_x in ((-1, 30), (1, 329)):
            with self.subTest(direction=direction), patch.object(APP_MODULE.time, "monotonic", return_value=2.0):
                self.pet.walk_direction = direction
                self.pet._actor_mirror = direction > 0
                self.pet._visual_mirror = direction > 0
                self.pet._draw()
                frame = self.pet._present_layered_frame.call_args.args[0]
                self.assertEqual(frame.size, (360, 430))
                self.assertEqual(frame.getbbox(), (expected_x, 394, expected_x + 1, 395))
                self.assertEqual(self.pet.current_sprite_bbox, frame.getbbox())
                self.assertEqual(frame.getpixel((0, 0)), (0, 0, 0, 0))
        # A right-facing exported source reverses the mirror decision.
        self._prepare_drawing(facing="right")
        self.pet.walk_direction = 1
        with patch.object(APP_MODULE.time, "monotonic", return_value=2.0):
            self.pet._draw()
        self.assertEqual(self.pet._present_layered_frame.call_args.args[0].getbbox(),
                         (30, 394, 31, 395))

    def test_quiet_status_mode_hides_only_an_existing_context_bubble(self) -> None:
        self.pet.settings = {"show_status_bubble": False}
        self.pet.bubble = SpeechBubble()
        self.pet.context_bubble_active = False
        self.pet.bubble.show("喵。", 3, now=10)
        self.pet._update_pet_context_bubble(10)
        self.assertEqual(self.pet.bubble.target_alpha, 1.0)
        self.pet.context_bubble_active = True
        self.pet._update_pet_context_bubble(10)
        self.assertEqual(self.pet.bubble.target_alpha, 0.0)
        self.assertFalse(self.pet.context_bubble_active)

    def test_walk_preserves_fractional_steps_at_the_24_fps_playback_rate(self) -> None:
        self.pet.root = Mock()
        self.pet.root.winfo_screenwidth.return_value = 1920
        self.pet.root.winfo_x.return_value = 100
        self.pet.root.winfo_y.return_value = 50
        self.pet.window_width = 360
        self.pet.behavior = SimpleNamespace(state="walk")
        self.pet.motion_pose = SimpleNamespace(state="walk", phase="loop", movement_weight=1.0)
        self.pet.walk_step_time = 0.0
        self.pet.walk_direction = 1
        self.pet.drag_start = None

        def update_position(geometry: str) -> None:
            self.pet.root.winfo_x.return_value = int(geometry.split("+")[1])

        self.pet.root.geometry.side_effect = update_position
        for tick in range(1, 25):
            self.pet._move_autonomously(tick / 24)
        self.assertEqual(self.pet.root.winfo_x(), 130)
        self.assertAlmostEqual(self.pet._walk_position_x, 130.0)

        self.pet.drag_start = (0, 0, 130, 50)
        self.pet.root.geometry.reset_mock()
        self.pet._move_autonomously(25 / 24)
        self.pet.root.geometry.assert_not_called()
        self.assertIsNone(self.pet._walk_position_x)

    def test_drag_release_preserves_sleep_until_explicit_wake(self) -> None:
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.manual_action("sleep")
        self.pet.did_drag = True
        self.pet.drag_start = (10, 10, 100, 100)
        self.pet._finish_drag(None)
        self.assertEqual(self.pet.behavior.state, "sleep")
        self.assertIsNone(self.pet.behavior.state_until)
        self.assertIsNone(self.pet.drag_start)

    def test_drawing_uses_the_motion_pose_frame_instead_of_restarting_a_clip(self) -> None:
        self._prepare_drawing()
        self.pet.walk_direction = -1
        self.pet.behavior = SimpleNamespace(state="idle", state_started=5.0)
        # The behavior can already be idle while the outgoing motion still owns
        # its final pose. Drawing must follow that pose, rather than the clock.
        self.pet.motion_pose = SimpleNamespace(
            state="groom", phase="play", frame_index=0, movement_weight=0.0,
        )
        with patch.object(APP_MODULE.time, "monotonic", return_value=5.1), \
                patch.object(self.pet.animation, "frame_at_index",
                             wraps=self.pet.animation.frame_at_index) as frame:
            self.pet._draw()
        self.assertEqual(frame.call_args.args[0], "groom")
        self.assertEqual(frame.call_args.args[1], 0)

    def test_outgoing_terminal_groom_pose_reaches_the_layered_surface(self) -> None:
        self._prepare_drawing()
        assets = self.directory_path / "cat"
        manifest = json.loads((assets / "clips.json").read_text(encoding="utf-8"))
        manifest["clips"]["groom"]["loop"] = False
        (assets / "clips.json").write_text(json.dumps(manifest), encoding="utf-8")
        # The first pose is red; the last pose is blue, on the fixed ground.
        for number, color in ((1, (255, 0, 0, 255)), (2, (0, 0, 255, 255))):
            sprite = Image.new("RGBA", (320, 300), (0, 0, 0, 0))
            sprite.putpixel((10, 282), color)
            sprite.save(assets / "groom" / f"{number:04d}.png")
        self.pet.animation = AnimationController(assets)
        self.pet.walk_direction = -1
        self.pet.behavior = SimpleNamespace(state="idle", state_started=2.0)
        self.pet.motion_pose = SimpleNamespace(
            state="groom", phase="play", frame_index=1, movement_weight=0.0,
        )
        with patch.object(APP_MODULE.time, "monotonic", return_value=2.0):
            self.pet._draw()
        presented = self.pet._present_layered_frame.call_args.args[0]
        self.assertEqual(presented.getpixel((30, 394)), (0, 0, 255, 255))

    def test_idle_keeps_the_whole_actor_mirrored_after_rightward_walk(self) -> None:
        self._prepare_drawing()
        self.pet._actor_mirror = True
        self.pet._visual_mirror = True
        self.pet._visual_state = "idle"
        self.pet.motion_pose = SimpleNamespace(state="idle", frame_index=0)
        self.pet._draw()
        self.assertEqual(self.pet._present_layered_frame.call_args.args[0].getbbox(),
                         (329, 394, 330, 395))

    def test_edge_stops_walk_without_flipping_direction_midstride(self) -> None:
        self.pet.root = Mock()
        self.pet.root.winfo_screenwidth.return_value = 1920
        self.pet.root.winfo_x.return_value = 9
        self.pet.root.winfo_y.return_value = 50
        self.pet.window_width = 360
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.state = "walk"
        self.pet.motion_pose = SimpleNamespace(movement_weight=1.0)
        self.pet.walk_step_time = 1.0
        self.pet.walk_direction = -1
        self.pet.drag_start = None
        self.pet._move_autonomously(1.1)
        self.assertEqual(self.pet.walk_direction, -1)
        self.assertEqual(self.pet.behavior.state, "idle")
        self.pet.root.geometry.assert_called_once_with("+8+50")

    def test_actual_drag_and_press_preserve_sleep_motion(self) -> None:
        self._prepare_drawing()
        self.pet.root = Mock()
        self.pet.root.winfo_screenwidth.return_value = 1920
        self.pet.root.winfo_screenheight.return_value = 1080
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.manual_action("sleep", now=1)
        self.pet.motion = MotionPlayer(self.pet.animation.motion_specs)
        self.pet.motion_pose = self.pet.motion.update("sleep", 1.0, started_at=1.0)
        self.pet.did_drag = False
        self.pet.drag_start = (0, 0, 100, 100)
        self.pet._drag(SimpleNamespace(x_root=1, y_root=1))
        self.assertEqual(self.pet.behavior.state, "sleep")
        self.pet._drag(SimpleNamespace(x_root=20, y_root=20))
        self.assertEqual(self.pet.behavior.state, "sleep")
        self.assertEqual(self.pet.motion_pose.state, "sleep")
        self.pet.root.geometry.assert_called_once_with("+120+120")

    def test_system_metrics_refresh_once_per_second_with_foreground_fps(self) -> None:
        self.pet.next_cpu_sample = 0
        self.pet.metrics.snapshot.return_value = MetricsSnapshot(
            cpu_percent=23.4, fps=63.2, fps_target="example-game.exe", fps_status="ok",
            cpu_temperature=57.5, temperature_status="ok")
        self.pet._sample_cpu(10)
        self.pet._sample_cpu(10.9)
        self.pet.metrics.snapshot.assert_called_once_with()
        self.assertEqual(self.pet.cpu_percent, 23.4)
        self.assertEqual(self.pet.next_cpu_sample, 11)
        self.pet._sample_cpu(11)
        self.assertEqual(self.pet.metrics.snapshot.call_count, 2)
        self.pet.settings = {"show_fps": True, "show_temperature": True}
        text = self.pet._context_status_text()
        self.assertIn("系统 CPU 23%", text)
        self.assertIn("FPS 63.2", text)
        self.assertIn("example-game", text)
        self.assertNotIn("CPU 温度", text)
        self.assertNotIn("GPU", text)
        self.assertIn("58°C", APP_MODULE.temperature_tooltip(self.pet.metrics_snapshot))

    def test_unavailable_foreground_fps_never_falls_back_to_pet_timer(self) -> None:
        self.pet.settings = {"show_fps": True, "show_temperature": False}
        self.pet.metrics_snapshot = MetricsSnapshot(fps_status="permission_denied")
        text = self.pet._context_status_text()
        self.assertIn("FPS --", text)
        self.assertNotIn("FPS 24", text)
        self.assertNotIn("CPU 温度", text)

    def test_expired_cpu_snapshot_is_not_kept_as_a_current_reading(self) -> None:
        self.pet.next_cpu_sample = 0
        self.pet.metrics.snapshot.return_value = MetricsSnapshot(cpu_percent=4, updated_at=1.0)
        self.pet._sample_cpu(10)
        self.assertIsNone(self.pet.cpu_percent)

    def test_temperature_moves_to_tray_but_weather_stays_in_bubble(self) -> None:
        self.pet.settings = {"show_fps": True, "show_temperature": True, "weather_city": "上海"}
        self.pet.weather_info = APP_MODULE.WeatherReading("上海", 26, 0, "晴")
        self.pet.cpu_percent = 42
        self.pet.metrics_snapshot = MetricsSnapshot(cpu_temperature=80, fps=60, fps_status="ok", fps_target="game.exe")
        text = self.pet._context_status_text()
        self.assertEqual(len(text.splitlines()), 3)
        self.assertIn("上海 晴 26°C", text)
        self.assertIn("系统 CPU 42%", text)
        self.assertIn("FPS 60.0", text)
        self.assertNotIn("80°C", text)
        self.assertNotIn("GPU", text)
        self.assertIn("80°C", APP_MODULE.temperature_tooltip(self.pet.metrics_snapshot))

    def test_old_weather_result_cannot_overwrite_a_new_same_name_location(self) -> None:
        self.pet.settings = {"weather_city": "Springfield", "weather_location": {"latitude": 40, "longitude": -90}}
        self.pet.weather_request_pending = True
        current = APP_MODULE.WeatherReading("Springfield", 20, 0, "晴")
        self.pet.weather_info = current
        old = APP_MODULE.WeatherReading("Springfield", 5, 3, "阴")
        self.pet._finish_weather("Springfield", old, None,
            location_signature=json.dumps({"latitude": 42, "longitude": -72}, sort_keys=True, ensure_ascii=False))
        self.assertIs(self.pet.weather_info, current)
        self.assertFalse(self.pet.weather_request_pending)
        self.assertEqual(self.pet.next_weather_refresh, 0)

    def test_manual_action_queues_wake_before_an_action_during_sleep(self) -> None:
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.manual_action("sleep")
        self.pet.motion = SimpleNamespace(is_idle=False)
        self.pet.bubble = SpeechBubble()
        self.pet.context_bubble_active = False
        self.pet._on_action = Mock()
        self.pet._choose_action("groom")
        self.assertEqual(self.pet.pending_action, "groom")
        self.assertEqual(self.pet.behavior.state, "idle")
        self.pet._on_action.assert_not_called()

    def test_queued_manual_action_waits_for_visual_exit_then_starts(self) -> None:
        self.pet.root = Mock()
        for name in ("_track_hover_pointer", "_sample_cpu", "_maybe_refresh_weather",
                     "_move_autonomously", "_update_pet_context_bubble", "_draw", "_on_action"):
            setattr(self.pet, name, Mock())
        self.pet.behavior = PetBehaviorManager()
        self.pet.behavior.autonomous_action = Mock(return_value=None)
        self.pet.motion = Mock(is_idle=True)
        self.pet.context_reactions = Mock()
        self.pet.context_reactions.poll.return_value = None
        self.pet.settings = {}
        self.pet.bubble = Mock()
        self.pet.next_status_refresh = float("inf")
        self.pet.pending_action = "groom"
        self.pet._animate()
        self.assertIsNone(self.pet.pending_action)
        self.assertEqual(self.pet.behavior.state, "groom")
        self.pet._on_action.assert_called_once()

    def test_tts_controls_and_calls_are_removed_from_main(self) -> None:
        source = (SOURCE_DIR / "desktop_ai_pet.pyw").read_text(encoding="utf-8-sig")
        for old_name in ("voice_reply_enabled", "WindowsSpeechOutput", "_speak_reply", "speech_output"):
            self.assertNotIn(old_name, source)

    def test_actual_tray_library_accepts_action_callbacks_and_captures_each_state(self) -> None:
        try:
            import pystray
        except ImportError:
            self.skipTest("pystray is needed for the Windows tray integration check")
        self.pet._schedule_ui = lambda callback: callback()
        self.pet._choose_action = Mock()
        menu = self.pet._tray_menu()
        actions = next(item for item in menu.items if item.submenu is not None)
        expected_states = ("click", "eat", "groom", "sleep", "idle")
        for item, expected in zip(actions.submenu.items, expected_states):
            item(None)
            self.pet._choose_action.assert_called_with(expected)
        self.assertEqual(self.pet._choose_action.call_count, len(expected_states))

    def test_autonomy_waits_for_visual_outro_to_finish(self) -> None:
        self.pet.root = Mock()
        self.pet._track_hover_pointer = Mock()
        self.pet._sample_cpu = Mock()
        self.pet._maybe_refresh_weather = Mock()
        self.pet._move_autonomously = Mock()
        self.pet._update_pet_context_bubble = Mock()
        self.pet._draw = Mock()
        self.pet.behavior = Mock(state="idle", state_started=1.0)
        self.pet.motion = Mock(is_idle=False)
        self.pet.context_reactions = Mock()
        self.pet.context_reactions.poll.return_value = None
        self.pet.cpu_percent = None
        self.pet.weather_info = None
        self.pet.settings = {}
        self.pet.bubble = Mock()
        self.pet.next_status_refresh = float("inf")
        self.pet._animate()
        self.pet.behavior.autonomous_action.assert_not_called()
        self.assertFalse(self.pet.context_reactions.poll.call_args.kwargs["is_idle"])
        self.pet.motion.update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
