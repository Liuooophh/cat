"""Ceramic button stays intact, with compact localized text and soft feedback."""
import unittest
from PIL import Image, ImageDraw
from pet_quick_voice import (HEIGHT, STATUS_WIDTH, TITLE_FONT_SIZE, HINT_FONT_SIZE,
                             render_control, render_feedback, quick_status_text,
                             _status_lines, status_font)


class QuickVoicePolishTests(unittest.TestCase):
    def test_recording_has_no_stop_badge_covering_ceramic_button(self):
        idle = render_control(HEIGHT, "", False, False, True)
        recording = render_control(HEIGHT, "", True, False, True)
        self.assertEqual(recording.tobytes(), idle.tobytes())

    def test_halo_is_under_the_opaque_ceramic_and_microphone(self):
        base = render_control(HEIGHT, "", True, False, True)
        active = render_feedback(base, "voice.recording", 1, 3, True)
        self.assertNotEqual(base.tobytes(), active.tobytes())
        for y in range(15, 57):
            for x in range(15, 57):
                if base.getpixel((x, y))[3] == 255:
                    self.assertEqual(base.getpixel((x, y)), active.getpixel((x, y)))

    def test_playing_halo_is_a_soft_filled_glow_instead_of_a_stroked_circle(self):
        blank = Image.new("RGBA", (HEIGHT, HEIGHT))
        glow = render_feedback(blank, "voice.playing", 0, 3, True)
        self.assertGreater(glow.getpixel((38, 38))[3], glow.getpixel((8, 38))[3])
        self.assertGreater(glow.getpixel((8, 38))[3], 0)
        self.assertEqual(glow.getpixel((0, 0))[3], 0)

    def test_processing_uses_bottom_dots_and_leaves_upper_icon_unchanged(self):
        base = render_control(HEIGHT, "", True, False, True)
        for phase in ("voice.transcribing", "voice.thinking", "voice.synthesizing"):
            frame = render_feedback(base, phase, 0, 3, True)
            self.assertEqual(base.crop((0, 0, HEIGHT, 60)).tobytes(), frame.crop((0, 0, HEIGHT, 60)).tobytes())
            self.assertNotEqual(base.tobytes(), frame.tobytes())

    def test_localized_two_line_prompts_fit_without_orphaned_characters(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (600, 152)))
        for language in ("zh-CN", "en", "ms"):
            for phase in ("voice.recording", "voice.transcribing", "voice.thinking",
                          "voice.synthesizing", "voice.playing"):
                with self.subTest(language=language, phase=phase):
                    text = quick_status_text(phase, language, "fallback")
                    lines = text.splitlines()
                    self.assertEqual(len(lines), 2)
                    for line, size, inset in zip(lines, (TITLE_FONT_SIZE, HINT_FONT_SIZE), (38, 22)):
                        font = status_font(size)
                        width = (STATUS_WIDTH-84-inset)*2
                        self.assertLessEqual(draw.textlength(line, font=font), width)
                        self.assertEqual(_status_lines(line, draw, font, width), [line])

    def test_compact_card_keeps_readable_titles_and_hints(self):
        self.assertLessEqual(STATUS_WIDTH-84, 190)
        self.assertGreaterEqual(TITLE_FONT_SIZE/2, 12)
        self.assertGreaterEqual(HINT_FONT_SIZE/2, 11)

    def test_listening_prompt_describes_actual_quick_cancel_behavior(self):
        text = quick_status_text("voice.recording", "zh-CN", "")
        self.assertEqual(text, "正在聆听…\n说完自动发送 · 再点取消")
        self.assertNotIn("结束", text)
        self.assertEqual(quick_status_text("unknown", "zh-CN", "原始错误"), "原始错误")


if __name__ == "__main__":
    unittest.main()
