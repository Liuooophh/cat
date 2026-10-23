"""Composition and compact-layout checks for the rendered overlay effects."""
from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from pet_presentation import SpeechBubble
from pet_visual_effects import draw_sleep_zzz


FONT = r"C:\Windows\Fonts\msyh.ttc"
TEXT = "01:23 · 吉隆坡 多云 28°C\n系统 CPU 23%\nFPS 60.0 · example-game"


def paint_card(alpha: float = 1, background=(0, 0, 0, 0), text: str = TEXT):
    frame = Image.new("RGBA", (360, 430), background)
    bubble = SpeechBubble()
    bubble.text, bubble.alpha = text, alpha
    bubble.draw(ImageDraw.Draw(frame), factor=1, width=360, font_path=FONT,
                anchor_bottom=189, status=True, frame=frame)
    return frame, bubble


class CardCompositionTests(unittest.TestCase):
    def test_partial_fade_composites_without_punching_alpha_holes_in_desktop(self):
        frame, _ = paint_card(0.35, (22, 31, 46, 255))
        self.assertEqual(frame.getchannel("A").getextrema(), (255, 255))

    def test_cached_text_and_card_fade_together_without_mutating_full_opacity_image(self):
        full, full_bubble = paint_card()
        half, half_bubble = paint_card(0.5)
        again, _ = paint_card()
        self.assertEqual(full.tobytes(), again.tobytes())
        self.assertEqual(full_bubble.last_bounds, half_bubble.last_bounds)
        for full_alpha, half_alpha in zip(full.getchannel("A").tobytes(), half.getchannel("A").tobytes()):
            self.assertLessEqual(abs(half_alpha - round(full_alpha * .5)), 1)

    def test_status_card_remains_three_rows_when_city_or_application_name_is_long(self):
        _, regular = paint_card()
        frame, long = paint_card(text="01:23 · " + "Long city name " * 15 + "\n系统 CPU 23%\nFPS 60.0 · " + "Long name " * 20)
        self.assertEqual(long.last_bounds[3] - long.last_bounds[1],
                         regular.last_bounds[3] - regular.last_bounds[1])
        self.assertLessEqual(long.last_bounds[2] - long.last_bounds[0], 302)
        self.assertLessEqual(frame.getbbox()[2], 360)
        self.assertLessEqual(long.last_bounds[3], 189)

    def test_sleep_card_has_clear_vertical_gap_from_sleep_marks(self):
        _, bubble = paint_card()
        blank = Image.new("RGBA", (360, 430))
        bounds = (38, 309, 277, 401)
        bubble.draw(ImageDraw.Draw(blank), factor=1, width=360, font_path=FONT,
                    anchor_bottom=bounds[1] - 28, status=True, frame=blank)
        marks = Image.new("RGBA", (360, 430))
        for time in (0, 1.2, 2.4, 3.6, 4.8):
            marks.paste((0, 0, 0, 0), (0, 0, *marks.size))
            draw_sleep_zzz(marks, bounds, time)
            self.assertGreaterEqual(marks.getbbox()[1], bubble.last_bounds[3] + 2)

    def test_existing_imagedraw_only_call_is_still_supported(self):
        frame = Image.new("RGBA", (360, 430))
        bubble = SpeechBubble()
        bubble.text, bubble.alpha = "喵。", 1
        bubble.draw(ImageDraw.Draw(frame), factor=1, width=360, font_path=FONT)
        self.assertIsNotNone(frame.getbbox())


class SleepMarkVisibilityTests(unittest.TestCase):
    def test_sleep_marks_never_disappear_during_an_animation_cycle(self):
        for step in range(25):
            with self.subTest(step=step):
                frame = Image.new("RGBA", (360, 430))
                draw_sleep_zzz(frame, (38, 309, 277, 401), step * .2)
                alpha = frame.getchannel("A")
                self.assertGreaterEqual(alpha.getextrema()[1], 210)
                self.assertGreater(sum(alpha.histogram()[181:]), 100)

    def test_sleep_marks_keep_opaque_underlying_cat_and_desktop_opaque(self):
        frame = Image.new("RGBA", (360, 430), (28, 35, 49, 255))
        draw_sleep_zzz(frame, (38, 309, 277, 401), 1.2)
        self.assertEqual(frame.getchannel("A").getextrema(), (255, 255))

    def test_mirrored_sleep_marks_follow_the_right_side_head(self):
        original, mirrored = Image.new("RGBA", (360, 430)), Image.new("RGBA", (360, 430))
        draw_sleep_zzz(original, (38, 309, 277, 401), .5)
        draw_sleep_zzz(mirrored, (38, 309, 277, 401), .5, mirror=True)
        self.assertLess(original.getbbox()[0], mirrored.getbbox()[0])
        self.assertGreater(mirrored.getbbox()[2], original.getbbox()[2])
        self.assertEqual(original.getbbox()[1], mirrored.getbbox()[1])
        self.assertEqual(original.getbbox()[3], mirrored.getbbox()[3])


if __name__ == "__main__":
    unittest.main()
