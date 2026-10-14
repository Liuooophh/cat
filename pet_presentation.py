"""Sprite loading, frame selection and the small comic speech bubble."""

from __future__ import annotations

import random
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


STATE_FPS = {"walk": 10.0, "sleep": 0.7}


class IdlePoseCycle:
    """Keeps idle poses calm and changes them with a short eased dissolve."""

    def __init__(self, frame_count: int, *, now: float | None = None) -> None:
        self.frame_count = max(1, frame_count)
        # The extracted idle frames are sorted: 01 is laughing, 02 is relaxed.
        self.current_index = 0
        self.transition_from: int | None = None
        self.transition_to: int | None = None
        self.transition_started: float | None = None
        self.transition_duration = 0.42
        now = time.monotonic() if now is None else now
        self.next_switch_at = now + self._next_interval(self.current_index)

    @staticmethod
    def _next_interval(index: int) -> float:
        # Let the laugh be the familiar default, with shorter relaxed breaks.
        return random.uniform(180.0, 240.0) if index == 0 else random.uniform(60.0, 90.0)

    def update(self, *, now: float, is_idle: bool) -> None:
        if self.frame_count < 2:
            return
        if self.transition_started is not None:
            if now - self.transition_started >= self.transition_duration:
                assert self.transition_to is not None
                self.current_index = self.transition_to
                self.transition_from = None
                self.transition_to = None
                self.transition_started = None
                self.next_switch_at = now + self._next_interval(self.current_index)
            return
        if is_idle and now >= self.next_switch_at:
            self.transition_from = self.current_index
            self.transition_to = (self.current_index + 1) % self.frame_count
            self.transition_started = now

    def frame_pair(self, *, now: float) -> tuple[int, int, float]:
        if self.transition_started is None:
            return self.current_index, self.current_index, 0.0
        assert self.transition_from is not None and self.transition_to is not None
        linear = min(1.0, max(0.0,
                              (now - self.transition_started) / self.transition_duration))
        # Smoothstep keeps the first and last frames from popping into motion.
        eased = linear * linear * (3.0 - 2.0 * linear)
        return self.transition_from, self.transition_to, eased


class AnimationController:
    """Loads user-provided PNG frames and normalizes them for the layered window."""

    def __init__(self, asset_dir: Path, cell_size: tuple[int, int] = (238, 244)) -> None:
        self.cell_size = cell_size
        self.frames: dict[str, list[Image.Image]] = {}
        for folder in asset_dir.iterdir() if asset_dir.exists() else ():
            if not folder.is_dir():
                continue
            paths = sorted(folder.glob("*.png"))
            if paths:
                images = []
                for path in paths:
                    with Image.open(path) as source:
                        images.append(self._fit(source.convert("RGBA")))
                self.frames[folder.name] = images
        if not self.frames.get("idle"):
            raise FileNotFoundError("没有找到 assets/pet/idle/ 下的 PNG 角色帧。")

    def _fit(self, source: Image.Image) -> Image.Image:
        max_w, max_h = self.cell_size
        scale = min(max_w / source.width, max_h / source.height)
        size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
        try:
            lanczos = Image.Resampling.LANCZOS
        except AttributeError:
            lanczos = Image.LANCZOS
        sprite = source.convert("RGBa").resize(size, lanczos).convert("RGBA")
        canvas = Image.new("RGBA", self.cell_size, (0, 0, 0, 0))
        canvas.alpha_composite(sprite, ((max_w - size[0]) // 2, max_h - size[1]))
        return canvas

    def frames_for(self, state: str) -> list[Image.Image]:
        return self.frames.get(state) or self.frames.get("idle", [])

    def frame(self, state: str, elapsed: float, selected_index: int = 0,
              *, blink: bool = False, mirror: bool = False) -> Image.Image:
        key = "blink" if state == "idle" and blink and self.frames.get("blink") else state
        frames = self.frames_for(key)
        if not frames:
            frames = self.frames_for("idle")
        fps = STATE_FPS.get(key)
        index = int(elapsed * fps) % len(frames) if fps else selected_index % len(frames)
        frame = frames[index]
        if mirror:
            return frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return frame

    def blend_frames(self, state: str, first_index: int, second_index: int,
                     amount: float) -> Image.Image:
        """Crossfade two poses in premultiplied alpha to keep cutout edges clean."""
        frames = self.frames_for(state)
        if not frames:
            raise FileNotFoundError(f"没有找到 {state} 动作图片。")
        first = frames[first_index % len(frames)].convert("RGBa")
        second = frames[second_index % len(frames)].convert("RGBa")
        return Image.blend(first, second, min(1.0, max(0.0, amount))).convert("RGBA")


class SpeechBubble:
    """One compact, dynamic comic bubble that fades quickly and never stacks."""

    def __init__(self) -> None:
        self.text = ""
        self.alpha = 0.0
        self.target_alpha = 0.0
        self.expires_at = 0.0
        self.last_tick = time.monotonic()

    def show(self, text: str, duration: float, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.text = text
        self.alpha = 0.0
        self.target_alpha = 1.0
        self.expires_at = now + duration
        self.last_tick = now

    def hide(self) -> None:
        self.target_alpha = 0.0
        self.expires_at = 0.0

    def tick(self, *, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if self.text and now >= self.expires_at:
            self.target_alpha = 0.0
        step = max(0.0, now - self.last_tick)
        self.last_tick = now
        rate = 1.0 / (0.17 if self.target_alpha else 0.19)
        if self.target_alpha:
            self.alpha = min(1.0, self.alpha + step * rate)
        else:
            self.alpha = max(0.0, self.alpha - step * rate)
            if self.alpha <= 0.0:
                self.text = ""
        return self.alpha

    @staticmethod
    def _wrap(text: str, font: ImageFont.FreeTypeFont,
              draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.splitlines() or [text]:
            line = ""
            for char in paragraph:
                candidate = line + char
                if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                    lines.append(line)
                    line = char
                else:
                    line = candidate
            lines.append(line)
        return lines or [text]

    @staticmethod
    @lru_cache(maxsize=8)
    def _font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            return ImageFont.load_default()

    def draw(self, draw: ImageDraw.ImageDraw, *, factor: int,
             width: int, font_path: str) -> None:
        if self.alpha <= 0.001 or not self.text:
            return
        alpha = round(255 * self.alpha)
        font = self._font(font_path, 14 * factor)

        max_bubble_width = width - 28
        pad_x, pad_y = 14 * factor, 9 * factor
        max_text_width = max_bubble_width * factor - pad_x * 2
        lines = self._wrap(self.text, font, draw, max_text_width)
        line_height = 20 * factor
        measured = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
        bubble_w = min(max_bubble_width * factor,
                       max(88 * factor, measured + pad_x * 2))
        bubble_h = max(42 * factor, len(lines) * line_height + pad_y * 2)
        x1 = (width * factor - bubble_w) // 2
        y1 = 12 * factor
        x2, y2 = x1 + bubble_w, y1 + bubble_h
        tail_x = width * factor // 2

        draw.polygon([(tail_x - 8 * factor, y2 - 1),
                      (tail_x, y2 + 8 * factor),
                      (tail_x + 8 * factor, y2 - 1)],
                     fill=(229, 222, 239, round(110 * self.alpha)))
        draw.polygon([(tail_x - 6 * factor, y2 - 2),
                      (tail_x, y2 + 6 * factor),
                      (tail_x + 6 * factor, y2 - 2)],
                     fill=(253, 251, 255, alpha))
        draw.rounded_rectangle(
            (x1 + factor, y1 + 2 * factor, x2 + factor, y2 + 3 * factor),
            radius=13 * factor, fill=(65, 48, 85, round(28 * self.alpha)),
        )
        draw.rounded_rectangle(
            (x1, y1, x2, y2), radius=13 * factor,
            fill=(253, 251, 255, alpha),
            outline=(231, 224, 241, round(245 * self.alpha)), width=factor,
        )
        total_h = len(lines) * line_height
        text_y = y1 + (bubble_h - total_h) // 2
        for line in lines:
            draw.text((width * factor // 2, text_y), line,
                      anchor="mt", font=font,
                      fill=(73, 61, 80, alpha))
            text_y += line_height
