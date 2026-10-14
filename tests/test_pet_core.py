from __future__ import annotations

import unittest
from datetime import datetime
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from pet_behavior import PetBehaviorManager
from pet_context import ContextReactionScheduler, CpuMonitor, fetch_weather
from pet_dialogue import DIALOGUE, DialogueManager
from pet_presentation import AnimationController, IdlePoseCycle, SpeechBubble
from tools.extract_bear_sprites import (
    all_components,
    baseline_artifacts,
    include_nearby_yellow_accents,
    largest_component_bbox,
    remove_baseline_artifacts,
)

APP_SOURCE = Path(__file__).resolve().parents[1] / "desktop_ai_pet.pyw"
APP_LOADER = SourceFileLoader("desktop_ai_pet_test_module", str(APP_SOURCE))
APP_SPEC = spec_from_loader(APP_LOADER.name, APP_LOADER)
APP_MODULE = module_from_spec(APP_SPEC)
APP_LOADER.exec_module(APP_MODULE)


class DialogueTests(unittest.TestCase):
    def test_dialogue_bank_has_requested_original_line_counts(self) -> None:
        expected = {
            "idle": 10, "happy": 8, "angry": 8, "laugh": 6, "yawn": 6,
            "sleep": 6, "clicked": 12, "surprised": 6, "confused": 6,
            "wave": 5, "interaction": 8,
        }
        self.assertEqual({key: len(DIALOGUE[key]) for key in expected}, expected)
        self.assertEqual(sum(len(DIALOGUE[key]) for key in expected), 81)

    def test_dialogue_avoids_immediate_repeat_and_sets_duration(self) -> None:
        manager = DialogueManager()
        first = manager.choose("idle", now=10)
        second = manager.choose("idle", now=11, force=True)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first[0], second[0])
        self.assertGreaterEqual(first[1], 2.3)
        self.assertLessEqual(first[1], 4.8)


class BehaviorTests(unittest.TestCase):
    def test_fifth_fast_click_triggers_anger_and_cooldown(self) -> None:
        manager = PetBehaviorManager()
        with patch("pet_behavior.random.random", return_value=0.0):
            outcomes = [manager.click(now=10 + index * 0.4) for index in range(5)]
        self.assertIsNotNone(outcomes[-1])
        self.assertEqual(outcomes[-1].state, "angry")
        self.assertIsNone(manager.click(now=12.1))

    def test_ambient_actions_wait_for_idle_and_jittered_check(self) -> None:
        manager = PetBehaviorManager()
        manager.next_autonomous_check = 0
        with patch("pet_behavior.random.random", return_value=0.0):
            action = manager.autonomous_action(now=100)
        self.assertIsNotNone(action)
        self.assertEqual(action.state, "idle")
        self.assertGreater(manager.next_autonomous_check, 100)
        manager.state = "sleep"
        manager.state_until = 500
        self.assertIsNone(manager.autonomous_action(now=101))

    def test_autonomous_walk_and_long_idle_sleep_are_reachable(self) -> None:
        manager = PetBehaviorManager()
        manager.next_autonomous_check = 0
        now = manager.last_activity + 10
        with patch("pet_behavior.random.random", return_value=0.34):
            walking = manager.autonomous_action(now=now)
        self.assertIsNotNone(walking)
        self.assertEqual(walking.state, "walk")

        manager = PetBehaviorManager()
        manager.last_activity = 0
        manager.next_autonomous_check = 0
        with patch("pet_behavior.random.random", return_value=0.0):
            sleeping = manager.autonomous_action(now=181)
        self.assertIsNotNone(sleeping)
        self.assertEqual(sleeping.state, "sleep")

    def test_context_reactions_only_start_while_idle(self) -> None:
        manager = PetBehaviorManager()
        manager.state = "walk"
        manager.state_until = 200
        self.assertIsNone(manager.context_action("confused", "cpu", now=100))
        manager.update(now=201)
        action = manager.context_action("confused", "cpu", now=202)
        self.assertIsNotNone(action)
        self.assertEqual((action.state, action.dialogue), ("confused", "cpu"))


class SpriteCleanupTests(unittest.TestCase):
    def test_removes_detached_baseline_and_keeps_feet(self) -> None:
        frame = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((25, 10, 95, 80), fill=(120, 70, 35, 255))
        draw.rectangle((30, 84, 90, 85), fill=(40, 30, 25, 220))

        self.assertEqual(baseline_artifacts(frame), [(30, 84, 91, 86)])
        cleaned = remove_baseline_artifacts(frame)
        self.assertEqual(cleaned.getpixel((50, 84))[3], 0)
        self.assertEqual(cleaned.getpixel((50, 80))[3], 255)

    def test_yellow_emphasis_marks_are_included_but_lower_labels_are_not(self) -> None:
        sheet = Image.new("RGBA", (240, 180), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        draw.rectangle((60, 55, 180, 165), fill=(120, 70, 35, 255))
        draw.polygon(((82, 43), (70, 20), (90, 38)), fill=(255, 200, 0, 255))
        draw.rectangle((90, 170, 150, 176), fill=(255, 200, 0, 255))
        components = all_components(sheet)
        body_box = largest_component_bbox(sheet)

        crop = include_nearby_yellow_accents(sheet, components, body_box)

        self.assertLessEqual(crop[1], 20)
        self.assertEqual(crop[3], body_box[3])

    def test_repaired_clicked_frames_retain_yellow_marks_near_top(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "assets" / "pet"
        frames = [asset_root / "clicked" / f"{index:02d}.png" for index in (6, 7)]
        for path in frames:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            top_region = image.crop((0, 0, image.width, round(image.height * 0.24)))
            yellow = sum(
                1 for red, green, blue, alpha in top_region.get_flattened_data()
                if alpha > 80 and red > 140 and green > 70 and blue < 145
                and red > green * 1.04 and green > blue * 1.25
            )
            self.assertGreater(yellow, 12, path.name)

    def test_extracted_frames_have_no_detached_baselines(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "assets" / "pet"
        flagged = []
        for path in sorted(asset_root.glob("*/*.png")):
            with Image.open(path) as source:
                if baseline_artifacts(source.convert("RGBA")):
                    flagged.append(path.relative_to(asset_root).as_posix())
        self.assertEqual(flagged, [])


class PetSizeTests(unittest.TestCase):
    def test_size_scale_is_clamped_to_safe_range(self) -> None:
        normalize = APP_MODULE.DesktopPet._normalize_pet_scale
        self.assertEqual(normalize(0.1), 0.5)
        self.assertEqual(normalize(1.0), 1.0)
        self.assertEqual(normalize(2.0), 1.5)
        self.assertEqual(normalize("invalid"), 1.0)

    def test_size_change_resizes_window_and_layered_surface(self) -> None:
        pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        pet.width, pet.height = 340, 410
        pet.window_width, pet.window_height = 340, 410
        pet.pet_scale = 1.0
        pet.settings = {}
        pet.root = Mock()
        pet.root.winfo_x.return_value = 400
        pet.root.winfo_y.return_value = 300
        pet.root.winfo_screenwidth.return_value = 1920
        pet.root.winfo_screenheight.return_value = 1080
        pet._create_surface_bitmap = Mock()

        pet._apply_pet_scale(1.5)

        self.assertEqual((pet.window_width, pet.window_height), (510, 615))
        self.assertEqual(pet.settings["pet_scale"], 1.5)
        pet._create_surface_bitmap.assert_called_once_with(510, 615)


class ClickHandlingTests(unittest.TestCase):
    def test_greeting_does_not_repeat_pet_name_after_speaker_prefix(self) -> None:
        pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        pet.settings = {"endpoint": "", "model": ""}
        greeting = pet._initial_greeting()

        self.assertNotIn("熊大", greeting)

    def test_pet_click_keeps_reaction_without_opening_chat(self) -> None:
        pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        pet.did_drag = False
        pet.drag_start = (1, 2, 3, 4)
        pet.behavior = Mock()
        pet.behavior.click.return_value = object()
        pet._on_action = Mock()
        pet.open_chat = Mock()

        pet._finish_drag(None)

        pet.behavior.click.assert_called_once_with()
        pet._on_action.assert_called_once()
        pet.open_chat.assert_not_called()
        self.assertIsNone(pet.drag_start)

    def test_second_mic_click_does_not_cancel_dictation(self) -> None:
        pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        pet.mic_listening = True
        pet.chat_status = Mock()
        pet.speech_input = Mock()
        pet._finish_speech_input = Mock()

        pet._toggle_speech_input()

        pet.speech_input.stop.assert_not_called()
        pet._finish_speech_input.assert_not_called()


class PresentationTests(unittest.TestCase):
    def test_idle_poses_switch_slowly_with_a_smooth_crossfade(self) -> None:
        with patch("pet_presentation.random.uniform", side_effect=[210.0, 75.0]):
            cycle = IdlePoseCycle(2, now=0.0)
            self.assertEqual(cycle.current_index, 0)  # laugh pose starts first
            cycle.update(now=209.0, is_idle=True)
            self.assertEqual(cycle.frame_pair(now=209.0), (0, 0, 0.0))

            cycle.update(now=210.0, is_idle=False)
            self.assertEqual(cycle.frame_pair(now=210.0), (0, 0, 0.0))
            cycle.update(now=211.0, is_idle=True)
            first, second, start_blend = cycle.frame_pair(now=211.0)
            middle_blend = cycle.frame_pair(now=211.21)[2]
            self.assertEqual((first, second), (0, 1))
            self.assertEqual(start_blend, 0.0)
            self.assertAlmostEqual(middle_blend, 0.5, places=1)

            cycle.update(now=211.43, is_idle=True)
            self.assertEqual(cycle.frame_pair(now=211.43), (1, 1, 0.0))
            self.assertAlmostEqual(cycle.next_switch_at, 286.43)

    def test_idle_starts_with_laugh_and_uses_longer_laugh_holds(self) -> None:
        with patch("pet_presentation.random.uniform", side_effect=[180.0, 60.0]):
            cycle = IdlePoseCycle(2, now=10.0)
            self.assertEqual(cycle.current_index, 0)
            self.assertEqual(cycle.next_switch_at, 190.0)
            cycle.update(now=190.0, is_idle=True)
            cycle.update(now=190.43, is_idle=True)
            self.assertEqual(cycle.current_index, 1)
            self.assertEqual(cycle.next_switch_at, 250.43)

    def test_supplied_frames_load_and_have_transparent_background(self) -> None:
        animation = AnimationController(Path(__file__).resolve().parents[1] / "assets" / "pet")
        self.assertEqual(len(animation.frames_for("walk")), 8)
        self.assertGreaterEqual(len(animation.frames_for("sleep")), 3)
        frame = animation.frame("idle", 0)
        self.assertEqual(frame.size, animation.cell_size)
        self.assertEqual(frame.getpixel((0, 0))[3], 0)

    def test_bubble_wraps_and_fades_to_one_layer(self) -> None:
        bubble = SpeechBubble()
        bubble.show("嗯……今天吃点啥呢？我正在认真思考一个大问题。(￣﹃￣)", 3, now=1)
        self.assertAlmostEqual(bubble.tick(now=1.17), 1.0)
        image = Image.new("RGBA", (680, 820), (0, 0, 0, 0))
        bubble.draw(ImageDraw.Draw(image), factor=2, width=340,
                    font_path=r"C:\Windows\Fonts\msyh.ttc")
        self.assertGreater(image.getbbox()[3], 0)
        self.assertEqual(bubble.tick(now=4.01), 0.0)
        self.assertEqual(bubble.text, "")

    def test_status_bubble_yields_to_dialogue_and_returns_when_idle(self) -> None:
        pet = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        pet.behavior = Mock(state="idle")
        pet.bubble = SpeechBubble()
        pet.context_bubble_active = False
        pet.hover_inside = False
        pet.chat_window = None
        pet.context_window = None
        pet._context_status_text = Mock(return_value="18:04 · 天气未设置 · CPU 1%")

        pet._update_pet_context_bubble(10.0)
        self.assertTrue(pet.context_bubble_active)
        self.assertEqual(pet.bubble.text, "18:04 · 天气未设置 · CPU 1%")

        pet._hide_context_bubble()
        pet.bubble.show("今天想吃点啥？", 3.0, now=10.0)
        pet._update_pet_context_bubble(10.1)
        self.assertFalse(pet.context_bubble_active)
        self.assertEqual(pet.bubble.text, "今天想吃点啥？")

        pet.bubble = SpeechBubble()
        pet._update_pet_context_bubble(20.0)
        self.assertTrue(pet.context_bubble_active)
        self.assertEqual(pet.bubble.text, "18:04 · 天气未设置 · CPU 1%")


class ContextTests(unittest.TestCase):
    def test_weather_fetch_resolves_city_and_current_conditions(self) -> None:
        responses = [
            {"results": [{"name": "上海", "latitude": 31.23, "longitude": 121.47}]},
            {"current": {"temperature_2m": 30.4, "weather_code": 61}},
        ]

        class Response:
            def __init__(self, data: dict[str, object]) -> None:
                self.data = data

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

            def read(self) -> bytes:
                import json
                return json.dumps(self.data).encode("utf-8")

        with patch("pet_context.urlopen", side_effect=lambda *_args, **_kwargs: Response(responses.pop(0))):
            reading = fetch_weather("上海")

        self.assertEqual(reading.city, "上海")
        self.assertEqual(reading.condition, "小雨")
        self.assertEqual(reading.dialogue_key, "weather_rain")
        self.assertTrue(reading.notable)

        scheduler = ContextReactionScheduler()
        reaction = scheduler.poll(
            now=8000, local_time=datetime(2026, 10, 1, 12),
            cpu_percent=None, weather=reading, is_idle=True,
            cpu_enabled=True, night_enabled=False,
        )
        self.assertIsNotNone(reaction)
        self.assertEqual(reaction.dialogue, "weather_rain")

    def test_context_scheduler_throttles_cpu_and_night_reactions(self) -> None:
        scheduler = ContextReactionScheduler()

        night = scheduler.poll(
            now=10_000, local_time=datetime(2026, 10, 1, 2, 0),
            cpu_percent=None, weather=None, is_idle=True,
            cpu_enabled=True, night_enabled=True,
        )
        self.assertIsNotNone(night)
        self.assertEqual(night.dialogue, "time")
        self.assertIsNone(scheduler.poll(
            now=11_000, local_time=datetime(2026, 10, 1, 2, 10),
            cpu_percent=None, weather=None, is_idle=True,
            cpu_enabled=True, night_enabled=True,
        ))

        scheduler = ContextReactionScheduler()
        scheduler.poll(
            now=20_000, local_time=datetime(2026, 10, 1, 12),
            cpu_percent=90.0, weather=None, is_idle=True,
            cpu_enabled=True, night_enabled=False,
        )
        self.assertIsNone(scheduler.poll(
            now=20_019, local_time=datetime(2026, 10, 1, 12),
            cpu_percent=90.0, weather=None, is_idle=True,
            cpu_enabled=True, night_enabled=False,
        ))
        cpu = scheduler.poll(
            now=20_020, local_time=datetime(2026, 10, 1, 12),
            cpu_percent=90.0, weather=None, is_idle=True,
            cpu_enabled=True, night_enabled=False,
        )
        self.assertIsNotNone(cpu)
        self.assertEqual(cpu.dialogue, "cpu")

    def test_cpu_monitor_calculates_busy_percentage_from_windows_counters(self) -> None:
        monitor = CpuMonitor()
        samples = iter(((0, 0, 0), (20, 100, 100)))

        def get_times(idle_ptr: object, kernel_ptr: object, user_ptr: object) -> int:
            idle, kernel, user = next(samples)
            idle_ptr._obj.dwLowDateTime = idle
            kernel_ptr._obj.dwLowDateTime = kernel
            user_ptr._obj.dwLowDateTime = user
            return 1

        monitor._get_system_times = get_times
        self.assertIsNone(monitor.sample())
        self.assertAlmostEqual(monitor.sample() or 0.0, 90.0)

if __name__ == "__main__":
    unittest.main()
