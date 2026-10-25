"""Focused regressions for the floating microphone and popup priority."""
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pet_quick_voice import (QuickVoiceControl, HEIGHT, STATUS_WIDTH, _PopupSurface,
                             control_placement, render_control, interaction_blocked)
from test_cat_app import APP_MODULE


class QuickVoicePopupTests(unittest.TestCase):
    def setUp(self):
        root = Mock()
        root.winfo_rootx.return_value = root.winfo_rooty.return_value = 100
        root.winfo_screenwidth.return_value = 800
        root.winfo_screenheight.return_value = 600
        self.app = SimpleNamespace(root=root, pet_scale=1, language="zh-CN",
                                   current_sprite_bbox=(10, 10, 110, 190))
        self.control = QuickVoiceControl(self.app)
        self.control.window = Mock()
        self.control.surface = Mock()

    def popup(self, name, state="normal"):
        popup = Mock()
        popup.winfo_exists.return_value = True
        popup.state.return_value = state
        setattr(self.app, name, popup)
        return popup

    def test_menu_hides_immediately_even_during_throttle(self):
        self.control.visible = True
        self.control._next_tick = float("inf")
        self.popup("context_window")
        self.control.tick(140, 140)
        self.control.window.withdraw.assert_called_once()
        self.assertFalse(self.control.visible)

    def test_all_settings_popups_have_priority(self):
        for name in ("companion_window", "settings_window", "speech_settings_window", "size_window"):
            with self.subTest(name=name):
                self.popup(name)
                self.assertTrue(interaction_blocked(self.app))
                setattr(self.app, name, None)

    def test_hidden_and_destroyed_popups_do_not_block(self):
        self.popup("settings_window", "withdrawn")
        self.popup("context_window").winfo_exists.return_value = False
        self.popup("size_window").state.side_effect = tk.TclError("destroyed")
        self.assertFalse(interaction_blocked(self.app))

    def test_active_turn_survives_popup_and_resumes_after_close(self):
        self.control.set_status("正在聆听")
        self.control.tick(700, 500)
        self.popup("context_window")
        self.control.tick(700, 500)
        self.assertFalse(self.control.visible)
        self.assertTrue(self.control.active)
        self.assertEqual(self.control.text, "正在聆听")
        self.app.context_window = None
        self.control.tick(700, 500)
        self.assertTrue(self.control.visible)

    def test_terminal_status_also_respects_menu(self):
        self.control.set_status("已取消", terminal=True)
        self.popup("context_window")
        self.control.tick(140, 140)
        self.control.window.deiconify.assert_not_called()
        self.assertFalse(self.control.visible)

    def test_unchanged_idle_does_not_redraw_each_tick(self):
        self.control._opacity = 1
        self.control.tick(140, 140)
        self.control._next_tick = 0
        self.control.tick(140, 140)
        self.control.surface.present.assert_called_once()

    def test_hover_changes_palette_without_recreating_window(self):
        self.control.tick(140, 140)
        before = self.control._signature
        self.control._next_tick = 0
        self.control.tick(260, 211)
        self.assertNotEqual(before, self.control._signature)
        self.control.window.deiconify.assert_called_once()

    def test_frame_bbox_changes_do_not_move_button_under_pointer(self):
        self.control.tick(140, 140)
        before = self.control._geometry
        self.app.current_sprite_bbox = (5, 6, 122, 202)
        self.control._next_tick = 0
        self.control.tick(140, 140)
        self.assertEqual(before, self.control._geometry)

    def test_left_side_status_expands_away_from_button(self):
        self.app.root.winfo_rootx.return_value = 600
        self.control.tick(650, 140)
        compact = self.control._geometry
        self.control.set_status("正在识别")
        self.control.tick(650, 140)
        expanded = self.control._geometry
        self.assertEqual(compact[0]+compact[2]-38, expanded[0]+expanded[2]-38)
        self.assertEqual(compact[1], expanded[1])

    def test_hidden_pet_blocks_even_active_voice(self):
        self.app.pet_hidden = True
        self.control.set_status("正在聆听")
        self.control.tick(140, 140)
        self.control.window.deiconify.assert_not_called()

    def test_leave_grace_fades_out_and_crossing_back_keeps_control_visible(self):
        with patch("pet_quick_voice.time.monotonic", return_value=100):
            self.control.tick(140, 140)
        with patch("pet_quick_voice.time.monotonic", return_value=100.2):
            self.control.tick(260, 211)
        with patch("pet_quick_voice.time.monotonic", return_value=100.4):
            self.control.tick(790, 590)
        self.assertTrue(self.control.visible)
        for index in range(1, 10):
            with patch("pet_quick_voice.time.monotonic", return_value=100.4+index*.15):
                self.control.tick(790, 590)
        self.assertFalse(self.control.visible)

    def test_microphone_and_cancel_clicks_use_existing_voice_actions(self):
        self.app._toggle_quick_voice = Mock()
        self.app._cancel_quick_voice = Mock()
        self.control.set_status("正在聆听")
        self.control.tick(140, 140)
        self.control._click(SimpleNamespace(x=38, y=38))
        self.control._click(SimpleNamespace(x=STATUS_WIDTH-14, y=19))
        self.app._toggle_quick_voice.assert_called_once()
        self.app._cancel_quick_voice.assert_called_once()


class NativeArtworkTests(unittest.TestCase):
    def test_real_popup_uses_no_activate_style_and_frees_surface(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        control = QuickVoiceControl(SimpleNamespace(root=root))
        self.addCleanup(control.close)
        control._create()
        self.assertEqual(control.window.state(), "withdrawn")
        get_style = control.surface.user32.GetWindowLongPtrW
        self.assertTrue(get_style(control.surface.hwnd, -20) & 0x08000000)

    def test_real_windows_alpha_surface_can_resize_hide_and_release(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        surface = _PopupSurface(win, HEIGHT)
        self.addCleanup(surface._release_layered_surface)
        for width in (HEIGHT, STATUS_WIDTH, HEIGHT):
            win.geometry(f"{width}x{HEIGHT}+0+0")
            win.update_idletasks()
            surface.present(render_control(width, "正在聆听", True, True, True))
            self.assertEqual((surface.surface_width, surface.surface_height), (width, HEIGHT))
        surface._release_layered_surface()
        self.assertIsNone(surface._dib)
        self.assertIsNone(surface._mem_dc)

    def test_artwork_has_transparent_corners_and_soft_alpha_for_all_states(self):
        for active in (False, True):
            for hovered in (False, True):
                frame = render_control(HEIGHT, "", active, hovered, True)
                self.assertEqual(frame.mode, "RGBA")
                self.assertEqual(frame.getpixel((0, 0))[3], 0)
                alpha = set(frame.getchannel("A").tobytes())
                self.assertIn(255, alpha)
                self.assertGreater(len(alpha), 40)

    def test_long_multilingual_status_fits_without_changing_button(self):
        for text in ("正在聆听", "AI is thinking "*12, "Sedang mengenali pertuturan "*12):
            frame = render_control(STATUS_WIDTH, text, False, False, True)
            plain = render_control(HEIGHT, "", False, False, True)
            self.assertEqual(frame.crop((0, 0, HEIGHT, HEIGHT)).tobytes(), plain.tobytes())

    def test_status_does_not_overlap_cat_at_screen_edges_and_narrow_layout(self):
        for pet, screen in (((40, 120, 240, 480), (1920, 1080)),
                            ((1610, 120, 1890, 480), (1920, 1080)),
                            ((230, 170, 570, 550), (800, 600)),
                            ((230, 50, 570, 400), (800, 600))):
            with self.subTest(pet=pet):
                (x, y, width), _side = control_placement(pet, screen)
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x+width, screen[0])
                self.assertLessEqual(y+HEIGHT, screen[1])
                overlap_w = max(0, min(x+width, pet[2])-max(x, pet[0]))
                overlap_h = max(0, min(y+HEIGHT, pet[3])-max(y, pet[1]))
                self.assertEqual(overlap_w*overlap_h, 0)

    def test_cat_identity_matches_silver_shaded_coat(self):
        self.assertIn("银渐层", APP_MODULE.SYSTEM_PROMPT)
        self.assertIn("英国短毛猫", APP_MODULE.SYSTEM_PROMPT)
        self.assertNotIn("狸花猫", APP_MODULE.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
