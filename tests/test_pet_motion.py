"""Verify gesture timing, safe interruptions and manifest boundaries."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from pet_behavior import PetBehaviorManager
from pet_motion import ClipSpec, MotionPlayer
from pet_presentation import AnimationController
from pet_visual_effects import BREATH_PERIOD, draw_sleep_zzz, sleep_breath


def specs():
    return {
        "idle": ClipSpec(10, 20, (0, 20), loop_blend_frames=3),
        "walk": ClipSpec(10, 60, (0, 60), stages={"enter": (0, 20), "loop": (20, 40), "exit": (40, 60)},
                         movement_range=(10, 45), loop_blend_frames=3),
        "sleep": ClipSpec(10, 60, (0, 60), stages={"enter": (0, 20), "loop": (20, 40), "exit": (40, 60)}),
        "eat": ClipSpec(10, 60, (0, 60), loop=False),
        "groom": ClipSpec(10, 60, (0, 50), loop=False),
    }


class MotionTests(unittest.TestCase):
    def test_idle_wrap_blends_only_after_the_first_cycle(self):
        player = MotionPlayer(specs())
        self.assertIsNone(player.sample(0).blend_from_index)
        pose = player.sample(2.0)
        self.assertEqual((pose.frame_index, pose.blend_from_index), (0, 19))
        self.assertGreater(pose.blend_amount, 0)
        self.assertIsNone(player.sample(2.3).blend_from_index)

    def test_staged_action_does_not_repeat_its_entry(self):
        player = MotionPlayer(specs())
        self.assertEqual(player.update("walk", 0).phase, "enter")
        self.assertEqual(player.update("walk", 2).frame_index, 20)
        self.assertIsNone(player.sample(2).blend_from_index)
        pose = player.update("walk", 4)
        self.assertEqual((pose.phase, pose.frame_index, pose.blend_from_index), ("loop", 20, 39))

    def test_stop_at_loop_boundary_uses_contiguous_outro(self):
        player = MotionPlayer(specs())
        player.update("walk", 0, started_at=0)
        player.update("idle", 3)
        pose = player.update("idle", 4)
        self.assertEqual((pose.phase, pose.frame_index), ("exit", 40))
        self.assertFalse(player.is_idle)
        self.assertEqual(player.update("idle", 6).frame_index, 59)
        self.assertEqual(player.update("idle", 6).frame_index, 59)
        self.assertEqual(player.update("idle", 6.1).state, "idle")

    def test_early_wake_finishes_entry_and_one_sleep_cycle(self):
        player = MotionPlayer(specs())
        player.update("sleep", 0)
        self.assertEqual(player.update("idle", 1).phase, "enter")
        self.assertEqual(player.update("idle", 2).phase, "loop")
        self.assertEqual(player.update("idle", 4).phase, "exit")

    def test_groom_plays_trimmed_terminal_once_and_does_not_restart(self):
        player = MotionPlayer(specs())
        player.update("groom", 0, started_at=0)
        self.assertEqual(player.update("groom", 8, started_at=0).frame_index, 49)
        self.assertEqual(player.update("groom", 8.1, started_at=0).state, "idle")
        self.assertEqual(player.update("groom", 9, started_at=0).state, "idle")
        self.assertEqual(player.update("groom", 10, started_at=10).state, "groom")

    def test_late_tick_delivers_terminal_before_idle(self):
        player = MotionPlayer(specs())
        player.update("walk", 0)
        pose = player.update("idle", 100)
        self.assertEqual((pose.phase, pose.frame_index), ("exit", 59))
        self.assertEqual(player.sample(100).frame_index, 59)
        self.assertTrue(player.sample(101).state == "idle")

    def test_drag_hard_reset_works_during_sleep_entry(self):
        player = MotionPlayer(specs())
        player.update("sleep", 0)
        self.assertEqual(player.force_idle(1).state, "idle")
        self.assertTrue(player.is_idle)

    def test_walk_moves_only_within_approved_frame_range(self):
        player = MotionPlayer(specs())
        player.update("walk", 0)
        self.assertEqual(player.sample(.9).movement_weight, 0)
        self.assertGreater(player.sample(1).movement_weight, 0)
        player.update("idle", 3)
        self.assertGreater(player.sample(4.1).movement_weight, 0)
        self.assertEqual(player.sample(4.5).movement_weight, 0)

    def test_new_action_does_not_interrupt_outro(self):
        player = MotionPlayer(specs())
        player.update("sleep", 0)
        player.update("idle", 3)
        self.assertEqual(player.update("walk", 4.1).state, "sleep")

    def test_nonfinite_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            MotionPlayer(specs()).sample(float("nan"))

    def test_complete_action_returns_to_matched_idle_phase_and_keeps_terminal(self):
        player = MotionPlayer(specs(), idle_entries={"groom": 7})
        player.update("groom", 0)
        self.assertEqual(player.update("idle", 5).frame_index, 49)
        pose = player.update("idle", 5.1)
        self.assertEqual((pose.state, pose.frame_index), ("idle", 7))
        self.assertEqual(player.sample(5.3).frame_index, 9)

    def test_manual_eat_plays_once_then_returns_to_idle(self):
        behavior = PetBehaviorManager(state_duration={"eat": 6})
        player = MotionPlayer(specs())
        behavior.manual_action("eat", now=0)
        phases = []
        for moment in range(61):
            now = moment / 10
            behavior.update(now=now)
            pose = player.update(behavior.state, now, started_at=behavior.state_started)
            phases.append(pose.phase)
        self.assertEqual(phases, ["once"] * 61)
        self.assertEqual(pose.frame_index, 59)
        behavior.update(now=6.1)
        self.assertEqual(
            player.update(behavior.state, 6.1, started_at=behavior.state_started).state,
            "idle",
        )

    def test_sleep_continues_for_hours_without_a_wake_request(self):
        player = MotionPlayer(specs())
        player.update("sleep", 0)
        self.assertEqual(player.update("sleep", 3600).phase, "loop")
        self.assertEqual(player.update("sleep", 7200).state, "sleep")

    def test_invalid_idle_entry_fails(self):
        for value in (20, -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MotionPlayer(specs(), idle_entries={"walk": value})


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "idle").mkdir()
        for n in range(1, 7):
            Image.new("RGBA", (8, 8)).save(self.root / "idle" / f"{n:04d}.png")
        self.manifest = {"format_version": 2, "cell_size": [8, 8], "clips": {
            "idle": {"fps": 10, "source_facing": "front", "frame_count": 6, "play_range": [1, 5]}}}

    def load(self):
        (self.root / "clips.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        return AnimationController(self.root, cache_capacity=2)

    def test_trimmed_range_and_bounded_cache(self):
        animation = self.load()
        self.assertAlmostEqual(animation.clip_duration("idle"), .4)
        for n in range(6):
            animation.frame_at_index("idle", n, mirror=True)
        self.assertLessEqual(animation.cached_frame_count, 2)
        self.assertEqual(animation.motion_spec("idle").play_range, (1, 5))

    def test_frame_count_mismatch_fails(self):
        self.manifest["clips"]["idle"]["frame_count"] = 7
        with self.assertRaises(ValueError):
            self.load()

    def test_discontinuous_stages_fail(self):
        self.manifest["clips"]["idle"]["stages"] = {
            "enter": [1, 2], "loop": [3, 4], "exit": [4, 5]}
        with self.assertRaises(ValueError):
            self.load()

    def test_out_of_range_and_bool_ranges_fail(self):
        for value in ([0, 8], [False, 3], [4, 2]):
            with self.subTest(value=value):
                self.manifest["clips"]["idle"]["play_range"] = value
                with self.assertRaises(ValueError):
                    self.load()

    def test_idle_return_match_uses_actual_terminal_pose_on_fixed_canvas(self):
        # A non-first idle frame matches the action's final color and alpha.
        for n in range(1, 7):
            Image.new("RGBA", (8, 8), (n * 30, 0, 0, 255)).save(
                self.root / "idle" / f"{n:04d}.png")
        (self.root / "groom").mkdir()
        Image.new("RGBA", (8, 8), (90, 0, 0, 255)).save(
            self.root / "groom" / "0001.png")
        self.manifest["clips"]["groom"] = {
            "fps": 10, "frame_count": 1, "loop": False}
        animation = self.load()
        self.assertEqual(animation.idle_entries["groom"], 2)
        self.assertEqual(animation.cell_size, (8, 8))
        self.assertLessEqual(animation.cached_frame_count, 2)


class SleepEffectTests(unittest.TestCase):
    def test_breath_only_moves_abdomen_and_is_periodic_without_mutating_source(self):
        sprite = Image.new("RGBA", (320, 300))
        draw = ImageDraw.Draw(sprite)
        draw.ellipse((20, 190, 260, 285), fill=(150, 110, 70, 255))
        before = sprite.tobytes()
        raised = sleep_breath(sprite, BREATH_PERIOD / 2)
        self.assertEqual(sprite.tobytes(), before)
        self.assertEqual(raised.size, sprite.size)
        changed = ImageChops.difference(sprite, raised).getbbox()
        self.assertIsNotNone(changed)
        self.assertGreaterEqual(changed[0], 100)
        self.assertLess(changed[3], 266)
        self.assertEqual(raised.crop((0, 0, 100, 300)).tobytes(),
                         sprite.crop((0, 0, 100, 300)).tobytes())
        self.assertEqual(raised.crop((0, 270, 320, 300)).tobytes(),
                         sprite.crop((0, 270, 320, 300)).tobytes())
        self.assertEqual(sleep_breath(sprite, BREATH_PERIOD).tobytes(), before)

    def test_mirrored_breath_preserves_head_on_the_right(self):
        sprite = Image.new("RGBA", (320, 300))
        ImageDraw.Draw(sprite).ellipse((20, 190, 260, 285), fill=(150, 110, 70, 255))
        raised = sleep_breath(sprite, BREATH_PERIOD / 2, mirror=True)
        self.assertEqual(raised.crop((185, 0, 320, 300)).tobytes(),
                         sprite.crop((185, 0, 320, 300)).tobytes())

    def test_zzz_is_above_the_cat_and_changes_over_time(self):
        first = Image.new("RGBA", (360, 430))
        second = first.copy()
        bounds = (40, 310, 280, 400)
        draw_sleep_zzz(first, bounds, 0.5)
        draw_sleep_zzz(second, bounds, 1.3)
        self.assertIsNotNone(first.getbbox())
        self.assertLess(first.getbbox()[3], 340)
        self.assertNotEqual(first.tobytes(), second.tobytes())


if __name__ == "__main__":
    unittest.main()
