"""Quiet floating voice control using the cat's existing per-pixel surface."""
from functools import lru_cache
import logging
import math
from pathlib import Path
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pet_i18n import tr
from pet_window_surface import LayeredSurface

HEIGHT = 76
STATUS_WIDTH = 268
TITLE_FONT_SIZE = 24  # Draw at 2x for crisp 12 px titles.
HINT_FONT_SIZE = 22
FADE_SECONDS = .18
LOG = logging.getLogger(__name__)


def interaction_contains(pointer, pet_rect, control_rect, margin=18):
    """Include the corridor between pet and control when crossing to the mic."""
    left = min(pet_rect[0], control_rect[0]) - margin
    top = min(pet_rect[1], control_rect[1]) - margin
    right = max(pet_rect[2], control_rect[2]) + margin
    bottom = max(pet_rect[3], control_rect[3]) + margin
    return left <= pointer[0] <= right and top <= pointer[1] <= bottom


def interaction_blocked(app):
    if getattr(app, "pet_hidden", False):
        return True
    for name in ("context_window", "companion_window", "settings_window",
                 "speech_settings_window", "size_window"):
        win = getattr(app, name, None)
        try:
            if win is not None and win.winfo_exists() and win.state() != "withdrawn":
                return True
        except tk.TclError:
            pass
    return False


def control_placement(pet, screen):
    """Reserve the status card up front, with no clamping back across the cat."""
    sw, sh = screen
    left, top, right, bottom = pet
    width = min(STATUS_WIDTH, max(HEIGHT, sw-16))
    y = round(min(max(8, top+(bottom-top)*.35), max(8, sh-HEIGHT-8)))
    if right+12+width <= sw-8:
        return (round(right+12), y, width), True
    if left-12-width >= 8:
        return (round(left-12-width), y, width), False
    x = round(min(max(8, (left+right-width)/2), max(8, sw-width-8)))
    # Narrow screens use a separate row instead of clamping text into the cat.
    above, below = top-12-HEIGHT, bottom+12
    if above >= 8:
        return (x, round(above), width), True
    if below+HEIGHT <= sh-8:
        return (x, round(below), width), True
    return (x, round(max(8, min(above, sh-HEIGHT-8))), width), True


@lru_cache(maxsize=1)
def microphone_artwork():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    with Image.open(base / "assets/ui/cream-cat-microphone.png") as image:
        return image.convert("RGBA").resize((60, 60), Image.Resampling.LANCZOS)


@lru_cache(maxsize=3)
def status_font(size=22):
    return ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", size)


def quick_status_text(phase, language, fallback):
    if phase in {"voice.recording", "voice.transcribing", "voice.thinking",
                 "voice.synthesizing", "voice.playing"}:
        return tr(phase.replace("voice.", "voice.quick."), language)
    return fallback


def _status_row(line, draw, font, width):
    if draw.textlength(line, font=font) > width:
        while line and draw.textlength(line+"…", font=font) > width:
            line = line[:-1]
        line += "…"
    return line


def _status_lines(text, draw, font, width):
    if "\n" in text:
        lines = []
        for line in text.splitlines()[:2]:
            lines.append(_status_row(line, draw, font, width))
        return lines
    lines, line = [], ""
    for character in text.replace("\n", " "):
        if draw.textlength(line+character, font=font) > width:
            lines.append(line)
            line = character
        else:
            line += character
    lines.append(line)
    if len(lines) > 2:
        while draw.textlength(lines[1]+"…", font=font) > width:
            lines[1] = lines[1][:-1]
        lines[1] += "…"
    return lines[:2]


@lru_cache(maxsize=24)
def render_control(width, text, active, hovered, on_right):
    """Cache antialiased raster states; never paint a colour-key rectangle."""
    frame = Image.new("RGBA", (width, HEIGHT))
    expanded = width > HEIGHT
    mic_x = 38 if on_right or not expanded else width-38
    if expanded:
        card = Image.new("RGBA", (width*2, HEIGHT*2))
        draw = ImageDraw.Draw(card)
        x0, x1 = (82, width-2) if on_right else (2, width-82)
        draw.rounded_rectangle((x0*2, 28, x1*2, 134), radius=25,
                               fill=(71, 47, 69, 28))
        draw.rounded_rectangle((x0*2, 22, x1*2, 128), radius=25,
                               fill="#fffaf7", outline="#dfd0ce", width=2)
        font = status_font(TITLE_FONT_SIZE)
        if "\n" in text:
            # Only the title reserves room for the close button. The hint can
            # use the full second row without shrinking its readable type.
            rows = text.splitlines()[:2]
            lines = [_status_row(row, draw, status_font(size), max(40, (x1-x0-inset)*2))
                     for row, size, inset in zip(rows, (TITLE_FONT_SIZE, HINT_FONT_SIZE), (38, 22))]
        else:
            lines = _status_lines(text, draw, font, max(40, (x1-x0-38)*2))
        ty = 63 if len(lines) == 1 else 44
        for index, line in enumerate(lines):
            line_font = status_font(HINT_FONT_SIZE) if index and "\n" in text else font
            draw.text((x0*2+22, ty+index*34), line, font=line_font,
                      fill="#64515e" if index == 0 else "#917983", anchor="lt")
        cx, cy = (x1-12)*2, 37
        draw.line((cx-5, cy-5, cx+5, cy+5), fill="#aa929e", width=2)
        draw.line((cx-5, cy+5, cx+5, cy-5), fill="#aa929e", width=2)
        frame.alpha_composite(card.resize(frame.size, Image.Resampling.LANCZOS))
    icon = microphone_artwork()
    if hovered:
        icon = ImageEnhance.Brightness(icon).enhance(1.035)
    frame.alpha_composite(icon, (mic_x-30, 8))
    return frame


def render_feedback(frame, phase, level, step, on_right):
    """Small animated overlay; the ceramic artwork and status card stay cached."""
    if phase not in {"voice.recording", "voice.transcribing", "voice.thinking",
                     "voice.synthesizing", "voice.playing"}:
        return frame
    overlay = Image.new("RGBA", (HEIGHT*2, HEIGHT*2))
    draw = ImageDraw.Draw(overlay)
    pulse = (math.sin(step*math.tau/12)+1)/2
    listening = phase == "voice.recording"
    color = (185, 119, 149) if listening else (162, 135, 174)
    x = 0 if on_right or frame.width == HEIGHT else frame.width-HEIGHT
    output = Image.new("RGBA", frame.size)
    if listening or phase == "voice.playing":
        # A blurred filled halo behind the ceramic, with no stroked ring.
        glow = Image.new("RGBA", overlay.size)
        radius = 27+pulse*.7+level*3 if listening else 28+pulse
        alpha = round(35+pulse*10+level*35) if listening else round(35+pulse*18)
        ImageDraw.Draw(glow).ellipse(((38-radius)*2, (38-radius)*2,
                                      (38+radius)*2, (38+radius)*2), fill=(*color, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        glow.putalpha(glow.getchannel("A").point(lambda value: value if value >= 3 else 0))
        output.alpha_composite(glow.resize((HEIGHT, HEIGHT), Image.Resampling.LANCZOS), (x, 0))
    output.alpha_composite(frame)
    if listening:
        # These bars use actual recorded PCM energy, not a fabricated waveform.
        for index, weight in enumerate((.5, .8, 1, .8, .5)):
            height = 2+round(level*10*weight)
            x = 26+index*5
            draw.rounded_rectangle((x*2, (73-height)*2, (x+3)*2, 146),
                                   radius=3, fill=(*color, 235))
    elif phase != "voice.playing":
        for index in range(3):
            radius = 2
            x = 30+index*8
            strength = ((math.cos(step*math.tau/12-index*math.tau/3)+1)/2)**2
            alpha = round(65+strength*155)
            draw.ellipse(((x-radius)*2, 138-radius*2, (x+radius)*2, 138+radius*2), fill=(*color, alpha))
    x = 0 if on_right or frame.width == HEIGHT else frame.width-HEIGHT
    output.alpha_composite(overlay.resize((HEIGHT, HEIGHT), Image.Resampling.LANCZOS), (x, 0))
    return output


class _PopupSurface(LayeredSurface):
    def __init__(self, window, width):
        self.root = window
        self.window_width, self.window_height = width, HEIGHT
        self._init_layered_surface()

    def present(self, frame):
        if frame.size != (self.window_width, self.window_height):
            self.window_width, self.window_height = frame.size
            self._create_surface_bitmap(*frame.size)
        self._present_layered_frame(frame)


class QuickVoiceControl:
    def __init__(self, app):
        self.app = app
        self.window = self.surface = None
        self.text = ""
        self.phase = ""
        self.active = self.visible = False
        self._next_tick = self._leave_at = self._terminal_until = 0.0
        self._opacity = 0.0
        self._last_tick = None
        self._placement = self._origin = self._geometry = self._signature = None
        self._retry_after = self._failures = 0
        self._meter = 0.0
        self._poll_job = None
        self._closed = False

    def start(self):
        """Poll Tk coordinates independently of animation/Win32 cursor updates."""
        if self._poll_job is None and not self._closed:
            self._poll_job = self.app.root.after(0, self._poll)

    def _poll(self):
        self._poll_job = None
        if self._closed:
            return
        try:
            self.tick(*self.app.root.winfo_pointerxy())
            delay = 500 if getattr(self.app, "pet_hidden", False) else (50 if self.visible or self.active else 100)
            self._poll_job = self.app.root.after(delay, self._poll)
        except tk.TclError:
            self._poll_job = None

    def _mapped(self, event):
        if event.widget is self.window:
            # The first native map can invalidate pixels submitted while hidden.
            self._signature = None
            self._next_tick = 0.0

    def hide(self):
        if self.window is not None and self.visible:
            self.window.withdraw()
        self.visible = False
        self._opacity = self._leave_at = 0.0
        self._meter = 0.0
        self._last_tick = self._placement = None

    def _create(self):
        self.window = win = tk.Toplevel(self.app.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.wm_attributes("-toolwindow", True)
        win.geometry(f"{HEIGHT}x{HEIGHT}+0+0")
        self.surface = _PopupSurface(win, HEIGHT)
        self.surface.present(Image.new("RGBA", (HEIGHT, HEIGHT)))
        # Revealing/clicking the mic must not steal focus from the foreground
        # game/editor, or change the app whose FPS is being monitored.
        user32 = self.surface.user32
        get_style = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_style = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_style(self.surface.hwnd, -20, get_style(self.surface.hwnd, -20) | 0x08000000)
        win.bind("<Button-1>", self._click)
        win.bind("<Escape>", lambda _e: self.app._cancel_quick_voice())
        win.bind("<Map>", self._mapped)

    def _click(self, event):
        width = self._geometry[2]
        mic_x = 38 if self._on_right or width == HEIGHT else width-38
        if (event.x-mic_x)**2+(event.y-38)**2 <= 32**2:
            self.app._toggle_quick_voice()
        elif width > HEIGHT:
            close_x = width-14 if self._on_right else width-96
            if abs(event.x-close_x) <= 12 and abs(event.y-19) <= 12:
                self.app._cancel_quick_voice()

    def set_status(self, text, *, terminal=False, phase=None):
        self.text = text
        self.phase = "" if terminal else (phase or "voice.recording")
        if self.phase != "voice.recording":
            self._meter = 0.0
        self.active = not terminal
        self._terminal_until = time.monotonic()+3.0 if terminal else 0.0
        self._next_tick = 0.0

    def tick(self, x, y):
        if self._closed or interaction_blocked(self.app):
            self.hide()
            self._next_tick = 0.0
            return
        now = time.monotonic()
        if now < self._retry_after:
            return
        if now < self._next_tick:
            return
        self._next_tick = now+.05
        app = self.app
        scale = getattr(app, "render_scale", app.pet_scale)
        rx, ry = app.root.winfo_rootx(), app.root.winfo_rooty()
        pet = tuple(value*scale+(rx if i % 2 == 0 else ry)
                    for i, value in enumerate(app.current_sprite_bbox))
        origin = (rx, ry, scale)
        if self._placement is None or origin != self._origin:
            self._placement = control_placement(pet, (app.root.winfo_screenwidth(), app.root.winfo_screenheight()))
            self._origin = origin
        (px, py, full_width), self._on_right = self._placement
        if self._terminal_until and now >= self._terminal_until:
            self.text, self._terminal_until = "", 0.0
        expanded = self.active or bool(self._terminal_until)
        width = full_width if expanded else HEIGHT
        if not expanded and not self._on_right:
            px += full_width-HEIGHT
        near = interaction_contains((x, y), pet, (px, py, px+width, py+HEIGHT))
        if near:
            self._leave_at = 0.0
        elif not self._leave_at:
            self._leave_at = now
        show = expanded or near or (self.visible and now-self._leave_at < .45)
        elapsed = .05 if self._last_tick is None else min(.1, now-self._last_tick)
        self._last_tick = now
        self._opacity = min(1.0, self._opacity+elapsed/FADE_SECONDS) if show else max(0, self._opacity-elapsed/FADE_SECONDS)
        if not self._opacity:
            self.hide()
            self._next_tick = now+.12
            return
        mic_x = 38 if self._on_right or not expanded else width-38
        hovered = (x-(px+mic_x))**2+(y-(py+38))**2 <= 32**2
        if self.phase == "voice.recording":
            try:
                value = float(getattr(getattr(app, "speech_input", None), "input_level", 0))
                value = min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0
            except (TypeError, ValueError):
                value = 0.0
            self._meter = self._meter*.55+value*.45
        meter = round(self._meter*12)
        step = int(now*10) % 12 if self.active else 0
        display_text = quick_status_text(self.phase, getattr(app, "language", "zh-CN"), self.text) if self.active else self.text
        signature = (width, display_text, self.active, hovered, self._on_right,
                     round(self._opacity*12), self.phase, meter, step)
        try:
            if self.window is None:
                self._create()
            geometry = (px, py, width)
            if geometry != self._geometry:
                self.window.geometry(f"{width}x{HEIGHT}+{px}+{py}")
                self.window.update_idletasks()
                self._geometry = geometry
            if not self.visible:
                self.window.deiconify()
                self.window.update_idletasks()
            if signature != self._signature or not self.visible:
                frame = render_control(width, display_text, self.active, hovered, self._on_right)
                if self.active:
                    frame = render_feedback(frame, self.phase, meter/12, step, self._on_right)
                if self._opacity < 1:
                    frame = frame.copy()
                    frame.putalpha(frame.getchannel("A").point(lambda a: round(a*self._opacity)))
                self.surface.present(frame)
                self._signature = signature
            self.window.configure(cursor="hand2" if hovered else "")
            self.visible = True
            self._retry_after = self._failures = 0
        except (OSError, tk.TclError):
            self._failures += 1
            LOG.warning("Cannot display quick voice control; will retry", exc_info=True)
            self.hide()
            self._destroy_window()
            self._retry_after = now+min(15, 2**min(self._failures-1, 4))

    def _destroy_window(self):
        if self.surface is not None:
            self.surface._release_layered_surface()
            self.surface = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
        self._geometry = self._signature = None
        self.visible = False

    def close(self):
        self._closed = True
        if self._poll_job is not None:
            try:
                self.app.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._destroy_window()


def run_diagnostics(argv=None):
    """Finite UI check with offscreen mapping, no mic, credentials or cloud."""
    import argparse
    import json
    from types import SimpleNamespace

    parser = argparse.ArgumentParser(description=run_diagnostics.__doc__)
    parser.add_argument("--quick-voice-diagnostics", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"frozen": bool(getattr(sys, "frozen", False)), "cloud_requests": 0,
              "microphone_recorded": False, "windows_shown": False}
    root = control = None
    code = 0
    try:
        root = tk.Tk()
        root.withdraw()
        app = SimpleNamespace(root=root, pet_scale=1, current_sprite_bbox=(10, 10, 110, 190),
                              speech_input=SimpleNamespace(input_level=.8))
        control = QuickVoiceControl(app)
        control._create()
        report["artwork_size"] = list(microphone_artwork().size)
        report["partial_alpha_levels"] = len(set(microphone_artwork().getchannel("A").tobytes()))
        for width, on_right in ((HEIGHT, True), (STATUS_WIDTH, True), (STATUS_WIDTH, False)):
            control.window.geometry(f"{width}x{HEIGHT}+0+0")
            control.window.update_idletasks()
            control.surface.present(render_control(width, "正在聆听", True, True, on_right))
        report["surface_resized"] = control.surface.surface_width == STATUS_WIDTH
        report["initially_withdrawn"] = control.window.state() == "withdrawn"
        report["no_activate"] = bool(control.surface.user32.GetWindowLongPtrW(control.surface.hwnd, -20) & 0x08000000)
        base = render_control(HEIGHT, "", True, False, True)
        report["meter_changes_with_level"] = (
            render_feedback(base, "voice.recording", 0, 0, True).tobytes() !=
            render_feedback(base, "voice.recording", 1, 0, True).tobytes())
        root.geometry(f"150x210+{root.winfo_screenwidth()+10000}+{root.winfo_screenheight()+10000}")
        root.overrideredirect(True)
        root.deiconify()
        root.update()
        point = (root.winfo_rootx()+40, root.winfo_rooty()+40)
        root.winfo_pointerxy = lambda: point
        control.start()
        root.update()
        report["first_hover_visible"] = control.visible and control.window.state() == "normal"
        report["native_windows_mapped_offscreen"] = True
        control.set_status("正在聆听", phase="voice.recording")
        control.tick(*point)
        report["meter_connected"] = control._meter > 0
        assert all(report[key] for key in ("surface_resized", "initially_withdrawn", "no_activate",
                                          "meter_changes_with_level", "first_hover_visible", "meter_connected"))
        report["status"] = "ok"
    except Exception as exc:
        report["status"], report["error"] = "error", str(exc)
        code = 1
    finally:
        if control is not None:
            control.close()
        if root is not None:
            root.destroy()
    args.quick_voice_diagnostics.parent.mkdir(parents=True, exist_ok=True)
    args.quick_voice_diagnostics.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return code
