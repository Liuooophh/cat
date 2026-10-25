"""Runtime checks for the bundled MP4 color-and-alpha animation assets."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pet_presentation import AnimationController
from pet_video import PackedAlphaVideo, default_video_plugin_path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "cat"


@unittest.skipUnless(sys.platform == "win32", "bundled decoder is Windows x64")
class PackedVideoTests(unittest.TestCase):
    def test_current_cat_package_contains_only_the_five_mp4_actions(self) -> None:
        manifest = json.loads((ASSETS / "clips.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["asset_format"], "mp4-color-alpha")
        self.assertEqual(
            set(manifest["clips"]), {"idle", "sleep", "click", "eat", "groom"},
        )
        self.assertEqual(
            {path.name for path in ASSETS.glob("*.mp4")},
            {"idle.mp4", "sleep.mp4", "click.mp4", "eat.mp4", "groom.mp4"},
        )
        self.assertEqual(list(ASSETS.rglob("*.png")), [])

    def test_decoder_seeks_and_rebuilds_rgba_without_a_background_box(self) -> None:
        with PackedAlphaVideo(
            ASSETS / "idle.mp4", default_video_plugin_path(), (320, 300),
        ) as video:
            first = video.frame(0)
            video.frame(30)
            repeated = video.frame(0)
            self.assertEqual((video.frame_count, video.fps, video.size), (240, 24.0, (320, 300)))
            self.assertEqual(repeated.tobytes(), first.tobytes())
            self.assertEqual(first.mode, "RGBA")
            self.assertEqual(first.getchannel("A").getextrema(), (0, 255))
            for point in ((0, 0), (319, 0), (0, 299), (319, 299)):
                self.assertEqual(first.getpixel(point)[3], 0)

    def test_controller_uses_declared_sleep_stages_and_bounded_cache(self) -> None:
        animation = AnimationController(ASSETS, cache_capacity=8)
        self.addCleanup(animation.close)
        self.assertTrue(animation.video_mode)
        self.assertEqual(animation.motion_spec("sleep").stages, {
            "enter": (0, 144), "loop": (144, 168), "exit": (168, 240),
        })
        self.assertEqual(animation.idle_entries, {
            "click": 0, "eat": 0, "groom": 0, "sleep": 0,
        })
        for state, index in (("idle", 0), ("click", 48), ("eat", 120),
                             ("groom", 120), ("sleep", 155)):
            frame = animation.frame_at_index(state, index)
            self.assertEqual(frame.size, (320, 300))
            self.assertLessEqual(animation.cached_frame_count, 8)


if __name__ == "__main__":
    unittest.main()
