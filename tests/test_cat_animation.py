"""Timing, canvas alignment and memory bounds for exported cat clips."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_presentation import AnimationController


class CatAnimationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.assets = Path(self.directory.name)
        clips = {
            "idle": {"fps": 2, "loop": True, "source_facing": "right"},
            "walk": {"fps": 4, "loop": True, "source_facing": "left"},
            "groom": {"fps": 2, "loop": False, "source_facing": "right"},
            "sleep": {"fps": 1, "loop": True, "source_facing": "left"},
        }
        self.manifest = {"cell_size": [12, 10], "ground_y": 9, "clips": clips}
        self._write_manifest()
        for state_number, state in enumerate(clips):
            folder = self.assets / state
            folder.mkdir()
            for i in range(4):
                frame = Image.new("RGBA", (12, 10), (0, 0, 0, 0))
                # Keep an asymmetric marker in its exported position. It must
                # survive playback without per-pose scaling or recentering.
                frame.putpixel((2, 8), (state_number * 40, i * 50, 150, 220))
                frame.save(folder / f"{i + 1:04d}.png")

    def _write_manifest(self) -> None:
        (self.assets / "clips.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")

    def test_initialization_and_frame_counts_do_not_decode_all_clips(self) -> None:
        with patch("pet_presentation.Image.open", wraps=Image.open) as image_open:
            animation = AnimationController(self.assets)
            self.assertEqual(len(animation.frames_for("idle")), 4)
            self.assertEqual(len(animation.frames_for("groom")), 4)
            self.assertEqual(animation.cached_frame_count, 0)
            image_open.assert_not_called()
            animation.frame("sleep", 0)
            self.assertEqual(image_open.call_count, 1)

    def test_missing_state_uses_idle_timing_instead_of_freezing(self) -> None:
        animation = AnimationController(self.assets)
        first = animation.frame("happy", 0, selected_index=3)
        second = animation.frame("happy", 0.6, selected_index=3)
        self.assertEqual(first.getpixel((2, 8))[1], 0)
        self.assertEqual(second.getpixel((2, 8))[1], 50)
        self.assertIs(second, animation.frame("idle", 0.6))
        self.assertEqual(animation.frame_rate("happy"), 2)

    def test_nonloop_groom_holds_its_last_frame_and_loop_wraps(self) -> None:
        animation = AnimationController(self.assets)
        self.assertEqual(animation.frame("groom", -1).getpixel((2, 8))[1], 0)
        self.assertEqual(animation.frame("groom", 1).getpixel((2, 8))[1], 100)
        self.assertEqual(animation.frame("groom", 2).getpixel((2, 8))[1], 150)
        self.assertIs(animation.frame("groom", 2), animation.frame("groom", 900))
        self.assertEqual(animation.frame("walk", 0.75).getpixel((2, 8))[1], 150)
        self.assertEqual(animation.frame("walk", 1).getpixel((2, 8))[1], 0)
        self.assertAlmostEqual(animation.clip_duration("groom"), 2)
        self.assertAlmostEqual(animation.frame_interval("walk"), 0.25)
        self.assertEqual(animation.source_facing("walk"), "left")

    def test_manifest_canvas_and_alpha_are_preserved_without_fitting(self) -> None:
        with patch.object(AnimationController, "_fit", side_effect=AssertionError("fit")):
            animation = AnimationController(self.assets, cell_size=(99, 88))
            frame = animation.frame("idle", 0)
        self.assertEqual(animation.cell_size, (12, 10))
        self.assertEqual(animation.ground_y, 9)
        self.assertFalse(animation.legacy_mode)
        self.assertEqual(frame.size, (12, 10))
        self.assertEqual(frame.getbbox(), (2, 8, 3, 9))
        self.assertEqual(frame.getpixel((2, 8)), (0, 0, 150, 220))
        mirrored = animation.frame("idle", 0, mirror=True)
        self.assertEqual(mirrored.getpixel((9, 8)), frame.getpixel((2, 8)))
        self.assertIs(mirrored, animation.frame("idle", 0, mirror=True))

    def test_decoded_frame_cache_is_bounded_and_reuses_recent_frames(self) -> None:
        animation = AnimationController(self.assets, cache_capacity=3)
        with patch("pet_presentation.Image.open", wraps=Image.open) as image_open:
            first = animation.frame("walk", 0)
            animation.frame("walk", 0.25)
            animation.frame("walk", 0.5)
            self.assertIs(animation.frame("walk", 0), first)
            animation.frame("walk", 0.75)
            self.assertIs(animation.frame("walk", 0), first)
            self.assertEqual(image_open.call_count, 4)
            # Frame 1 was evicted, while the recently reused first frame stayed.
            animation.frame("walk", 0.25)
            self.assertEqual(image_open.call_count, 5)
        for state in self.manifest["clips"]:
            for elapsed in (0.0, 0.5, 1.0, 1.5):
                animation.frame(state, elapsed, mirror=True)
                self.assertLessEqual(animation.cached_frame_count, 3)

    def test_wrong_export_size_fails_instead_of_rescaling_a_pose(self) -> None:
        Image.new("RGBA", (8, 8)).save(self.assets / "walk" / "0001.png")
        animation = AnimationController(self.assets)
        with self.assertRaisesRegex(ValueError, "预期为"):
            animation.frame("walk", 0)

    def test_old_assets_keep_pose_selection_and_default_canvas(self) -> None:
        (self.assets / "clips.json").unlink()
        animation = AnimationController(self.assets)
        self.assertTrue(animation.legacy_mode)
        self.assertEqual(animation.cell_size, (238, 244))
        self.assertEqual(animation.frame("idle", 10, 1).size, (238, 244))
        self.assertIs(animation.frame("idle", 10, 1), animation.frames_for("idle")[1])
        self.assertIs(animation.frame("walk", 0.1), animation.frames_for("walk")[1])


if __name__ == "__main__":
    unittest.main()
