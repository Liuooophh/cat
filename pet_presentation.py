"""Sprite loading, frame selection and the small comic speech bubble."""

from __future__ import annotations

import json
import math
import random
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from pet_motion import ClipSpec


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


@dataclass(frozen=True)
class _Clip:
    paths: tuple[Path, ...]
    fps: float | None
    loop: bool
    source_facing: str


class _ClipFrames(Sequence[Image.Image]):
    """Expose frame counts/indexing without decoding a complete video on startup."""

    def __init__(self, animation: AnimationController, state: str) -> None:
        self.animation = animation
        self.state = state

    def __len__(self) -> int:
        return len(self.animation._clips[self.state].paths)

    def __getitem__(self, index: int | slice) -> Image.Image | list[Image.Image]:
        if isinstance(index, slice):
            return [self.animation._load_frame(self.state, i)
                    for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("animation frame index out of range")
        return self.animation._load_frame(self.state, index)


class AnimationController:
    """Play timed PNG clips on one fixed canvas, with a bounded decoded-frame cache.

    ``clips.json`` supplies ``cell_size``, optional ``ground_y``, and a ``clips``
    mapping. Each clip uses PNGs in its named subdirectory and declares ``fps``,
    ``loop`` and ``source_facing``. Exported frames already share the canvas and
    ground position, so playback never fits individual poses to their bounds.
    Older assets without a manifest retain their original sizing/pose behavior.
    """

    def __init__(self, asset_dir: Path, cell_size: tuple[int, int] = (238, 244),
                 *, cache_capacity: int = 96) -> None:
        self.cell_size = cell_size
        self.ground_y = cell_size[1]
        self.cache_capacity = max(1, int(cache_capacity))
        self._cache: OrderedDict[tuple[str, int, bool], Image.Image] = OrderedDict()
        self._clips: dict[str, _Clip] = {}
        self.motion_specs: dict[str, ClipSpec] = {}
        self._idle_entries: dict[str, int] | None = None
        manifest_path = asset_dir / "clips.json"
        self.legacy_mode = not manifest_path.is_file()
        if self.legacy_mode:
            for folder in sorted(asset_dir.iterdir()) if asset_dir.exists() else ():
                if folder.is_dir():
                    paths = tuple(sorted(folder.glob("*.png")))
                    if paths:
                        self._clips[folder.name] = _Clip(
                            paths, STATE_FPS.get(folder.name), True, "left")
        else:
            self._read_manifest(asset_dir, manifest_path)
        if "idle" not in self._clips:
            raise FileNotFoundError(f"没有找到 {asset_dir / 'idle'} 下的 PNG 角色帧。")
        for state, clip in self._clips.items():
            self.motion_specs.setdefault(state, ClipSpec(
                clip.fps or 1.0, len(clip.paths), (0, len(clip.paths)),
                clip.loop, clip.source_facing))
        self.frames: dict[str, Sequence[Image.Image]] = {
            state: _ClipFrames(self, state) for state in self._clips
        }

    def _read_manifest(self, asset_dir: Path, manifest_path: Path) -> None:
        with manifest_path.open(encoding="utf-8-sig") as stream:
            manifest = json.load(stream)
        size = manifest.get("cell_size")
        if (not isinstance(size, list) or len(size) != 2
                or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0
                       for n in size)):
            raise ValueError("clips.json 的 cell_size 必须是两个正整数。")
        self.cell_size = (size[0], size[1])
        self.ground_y = manifest.get("ground_y", self.cell_size[1])
        if not isinstance(self.ground_y, int) or not 0 <= self.ground_y <= size[1]:
            raise ValueError("clips.json 的 ground_y 必须位于画布高度范围内。")
        clips = manifest.get("clips")
        if not isinstance(clips, dict):
            raise ValueError("clips.json 缺少 clips 动作列表。")
        for state, metadata in clips.items():
            if (not isinstance(state, str) or not state
                    or Path(state).name != state or state in {".", ".."}):
                raise ValueError("clips.json 的动作名必须是一个文件夹名称。")
            if not isinstance(metadata, dict):
                raise ValueError(f"{state} 动作配置必须是对象。")
            fps = metadata.get("fps", 24.0)
            if (not isinstance(fps, (int, float)) or isinstance(fps, bool)
                    or not math.isfinite(fps) or fps <= 0):
                raise ValueError(f"{state} 动作的 fps 必须是正数。")
            loop = metadata.get("loop", True)
            facing = metadata.get("source_facing", "left")
            if not isinstance(loop, bool) or facing not in {"left", "right", "front"}:
                raise ValueError(f"{state} 动作的 loop 或 source_facing 配置无效。")
            paths = tuple(sorted((asset_dir / state).glob("*.png")))
            if not paths:
                raise FileNotFoundError(f"{state} 动作文件夹内没有 PNG 帧。")
            expected = metadata.get("frame_count", metadata.get("frames", len(paths)))
            if isinstance(expected, bool) or not isinstance(expected, int) or expected != len(paths):
                raise ValueError(f"{state} 帧数与清单不一致。")
            if "frame_count" in metadata or "frames" in metadata:
                if [p.name for p in paths] != [f"{i:04d}.png" for i in range(1, expected + 1)]:
                    raise ValueError(f"{state} 帧文件必须从 0001.png 连续编号。")
            def frame_range(value: object, label: str) -> tuple[int, int]:
                if (not isinstance(value, (list, tuple)) or len(value) != 2
                        or any(not isinstance(n, int) or isinstance(n, bool) for n in value)
                        or not 0 <= value[0] < value[1] <= len(paths)):
                    raise ValueError(f"{state} 的 {label} 必须是有效的左闭右开帧范围。")
                return (value[0], value[1])
            play_range = frame_range(metadata.get("play_range", [0, len(paths)]), "play_range")
            stages_data = metadata.get("stages", {})
            if not isinstance(stages_data, dict):
                raise ValueError(f"{state} 的 stages 必须是对象。")
            stages = {name: frame_range(bounds, name) for name, bounds in stages_data.items()}
            if stages:
                if (set(stages) != {"enter", "loop", "exit"}
                        or stages["enter"][0] != play_range[0]
                        or stages["enter"][1] != stages["loop"][0]
                        or stages["loop"][1] != stages["exit"][0]
                        or stages["exit"][1] != play_range[1]):
                    raise ValueError(f"{state} 的 enter/loop/exit 必须连续覆盖 play_range。")
            movement = metadata.get("movement_range")
            movement = frame_range(movement, "movement_range") if movement is not None else None
            if movement and not play_range[0] <= movement[0] < movement[1] <= play_range[1]:
                raise ValueError(f"{state} 的 movement_range 必须位于 play_range 内。")
            blend = metadata.get("loop_blend_frames", 0)
            loop_range = stages.get("loop", play_range)
            if (not isinstance(blend, int) or isinstance(blend, bool)
                    or not 0 <= blend < loop_range[1] - loop_range[0]):
                raise ValueError(f"{state} 的 loop_blend_frames 无效。")
            self._clips[state] = _Clip(paths, float(fps), loop, facing)
            self.motion_specs[state] = ClipSpec(float(fps), len(paths), play_range, loop,
                                                facing, stages, movement, blend)

    @property
    def cached_frame_count(self) -> int:
        return len(self._cache)

    def _clip_key(self, state: str) -> str:
        return state if state in self._clips else "idle"

    def _load_frame(self, state: str, index: int, mirror: bool = False) -> Image.Image:
        key = (state, index, mirror)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if mirror:
            frame = self._load_frame(state, index).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        else:
            path = self._clips[state].paths[index]
            with Image.open(path) as source:
                frame = source.convert("RGBA")
            if self.legacy_mode:
                frame = self._fit(frame)
            elif frame.size != self.cell_size:
                raise ValueError(f"{path} 尺寸为 {frame.size}，预期为 {self.cell_size}。")
        self._cache[key] = frame
        while len(self._cache) > self.cache_capacity:
            self._cache.popitem(last=False)
        return frame

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

    def frames_for(self, state: str) -> Sequence[Image.Image]:
        return self.frames[self._clip_key(state)]

    def frame_rate(self, state: str) -> float | None:
        return self._clips[self._clip_key(state)].fps

    def frame_interval(self, state: str) -> float | None:
        fps = self.frame_rate(state)
        return 1.0 / fps if fps else None

    def clip_duration(self, state: str) -> float | None:
        clip = self._clips[self._clip_key(state)]
        return self.motion_spec(state).duration if clip.fps else None

    def motion_spec(self, state: str) -> ClipSpec:
        return self.motion_specs[self._clip_key(state)]

    @property
    def idle_entries(self) -> dict[str, int]:
        """Find one fixed return pose per action, without changing frame layout.

        Small premultiplied thumbnails compare color and silhouette once at
        startup. Rendering still uses the untouched, fixed-size PNG canvases;
        there is no per-frame fitting or bounding-box alignment.
        """
        if self._idle_entries is None:
            idle = self.motion_spec("idle")
            first, end = idle.play_range

            def thumbnail(state: str, index: int) -> Image.Image:
                with Image.open(self._clips[state].paths[index]) as source:
                    return source.convert("RGBa").resize((48, 45), Image.Resampling.BOX)

            targets = {
                state: thumbnail(state, spec.play_range[1] - 1)
                for state, spec in self.motion_specs.items() if state != "idle"
            }
            best = {state: (float("inf"), first) for state in targets}
            for index in range(first, end):
                candidate = thumbnail("idle", index)
                for state, target in targets.items():
                    delta = ImageStat.Stat(ImageChops.difference(candidate, target)).mean
                    # Alpha gives the fixed silhouette and ground contact more
                    # weight than small changes in fur texture or lighting.
                    score = sum(delta[:3]) / 3.0 + delta[3] * 2.0
                    if score < best[state][0]:
                        best[state] = (score, index)
            self._idle_entries = {state: value[1] for state, value in best.items()}
            for state, index in self._idle_entries.items():
                self._load_frame(state, self.motion_spec(state).play_range[1] - 1)
                self._load_frame("idle", index)
        return dict(self._idle_entries)

    def frame_at_index(self, state: str, index: int, *, mirror: bool = False) -> Image.Image:
        key = self._clip_key(state)
        if not 0 <= index < len(self._clips[key].paths):
            raise IndexError("animation frame index out of range")
        return self._load_frame(key, index, mirror)

    def source_facing(self, state: str) -> str:
        return self._clips[self._clip_key(state)].source_facing

    def frame(self, state: str, elapsed: float, selected_index: int = 0,
              *, blink: bool = False, mirror: bool = False) -> Image.Image:
        key = "blink" if state == "idle" and blink and self.frames.get("blink") else state
        key = self._clip_key(key)
        clip = self._clips[key]
        index = int(max(0.0, elapsed) * clip.fps) if clip.fps else selected_index
        first, end = self.motion_spec(key).play_range
        if clip.loop:
            index = first + index % (end - first)
        else:
            index = min(end - 1, first + max(0, index))
        return self._load_frame(key, index, mirror)

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
    """A quiet information card, composited as one consistently fading layer."""

    def __init__(self) -> None:
        self.text = ""
        self.alpha = 0.0
        self.target_alpha = 0.0
        self.expires_at = 0.0
        self.last_tick = time.monotonic()
        self.last_bounds: tuple[int, int, int, int] | None = None

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

    @staticmethod
    def _ellipsis(text: str, font: ImageFont.ImageFont,
                  draw: ImageDraw.ImageDraw, max_width: int) -> str:
        if draw.textlength(text, font=font) <= max_width:
            return text
        first, last = 0, len(text)
        while first < last:
            middle = (first + last + 1) // 2
            if draw.textlength(text[:middle] + "…", font=font) <= max_width:
                first = middle
            else:
                last = middle - 1
        return text[:first].rstrip() + "…"

    @staticmethod
    @lru_cache(maxsize=16)
    def _render_card(text: str, font_path: str, width: int,
                     factor: int, status: bool) -> tuple[Image.Image, int, int, int]:
        from PIL import ImageFilter

        # Render only when text/layout changes, at 3x for clean glyphs and
        # rounded corners. Cached images are never faded or mutated in place.
        quality = 3
        scale = factor * quality
        maximum = max(60, min(302, width - 28)) * scale
        pad_x, pad_y, bleed = 13 * scale, (8 if status else 10) * scale, 8 * scale
        body_font = SpeechBubble._font(font_path, (13 if status else 14) * scale)
        header_font = SpeechBubble._font(font_path, 12 * scale) if status else body_font
        measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        rows: list[tuple[str, ImageFont.ImageFont, bool]] = []
        for index, paragraph in enumerate(text.splitlines() or [text]):
            if status and index >= 3:
                break
            is_header = status and index == 0
            font = header_font if is_header else body_font
            if status:
                rows.append((SpeechBubble._ellipsis(paragraph, font, measure, maximum - pad_x * 2),
                             font, is_header))
            else:
                rows.extend((line, font, False) for line in SpeechBubble._wrap(
                    paragraph, font, measure, maximum - pad_x * 2))
        measured = max(measure.textlength(line, font=font) for line, font, _ in rows)
        card_w = min(maximum, max((202 if status else 82) * scale, round(measured) + pad_x * 2))
        heights = [(17 if header else 19 if status else 21) * scale for _, _, header in rows]
        header_gap = 3 * scale if status and len(rows) > 1 else 0
        card_h = max(35 * scale, sum(heights) + header_gap + pad_y * 2)
        canvas_size = card_w + bleed * 2, card_h + bleed * 2
        shadow = Image.new("RGBA", canvas_size)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (bleed, bleed + 3 * scale, bleed + card_w, bleed + card_h + 3 * scale),
            radius=12 * scale, fill=(39, 49, 67, 21))
        shadow = shadow.filter(ImageFilter.GaussianBlur(2.5 * scale))
        card = Image.new("RGBA", canvas_size)
        pen = ImageDraw.Draw(card)
        pen.rounded_rectangle((bleed, bleed, bleed + card_w, bleed + card_h),
                              radius=12 * scale, fill=(250, 252, 255, 250),
                              outline=(218, 226, 236, 235), width=scale)
        ink = Image.new("RGBA", canvas_size)
        pen = ImageDraw.Draw(ink)
        y = bleed + pad_y
        for index, ((line, font, header), height) in enumerate(zip(rows, heights)):
            pen.text((bleed + pad_x, y), line, font=font, anchor="lt",
                     fill=(105, 116, 132, 255) if header else (54, 66, 85, 255))
            y += height
            if index == 0 and header_gap:
                pen.line((bleed + pad_x, y + scale, bleed + card_w - pad_x, y + scale),
                         fill=(227, 233, 241, 220), width=scale)
                y += header_gap
        # Drawing anti-aliased letters directly into a translucent RGBA card
        # replaces its alpha at glyph edges. Compositing the ink preserves the
        # card beneath them and keeps text solid throughout the fade.
        card.alpha_composite(ink)
        shadow.alpha_composite(card)
        result_size = canvas_size[0] // quality, canvas_size[1] // quality
        result = shadow.convert("RGBa").resize(result_size, Image.Resampling.LANCZOS).convert("RGBA")
        return result, card_w // quality, card_h // quality, bleed // quality

    def draw(self, draw: ImageDraw.ImageDraw, *, factor: int,
             width: int, font_path: str, anchor_bottom: int | None = None,
             status: bool = False, frame: Image.Image | None = None) -> None:
        if self.alpha <= 0.001 or not self.text:
            self.last_bounds = None
            return
        card, bubble_w, bubble_h, bleed = self._render_card(
            self.text, font_path, width, max(1, factor), status)
        x1 = (width * factor - bubble_w) // 2
        y1 = (max(10 * factor, anchor_bottom * factor - bubble_h)
              if anchor_bottom is not None else 10 * factor)
        self.last_bounds = x1, y1, x1 + bubble_w, y1 + bubble_h
        if self.alpha < 1.0:
            card = card.copy()
            alpha = max(0.0, min(1.0, self.alpha))
            card.putalpha(card.getchannel("A").point(lambda value: round(value * alpha)))
        # Existing callers passing ImageDraw remain supported. The application
        # supplies frame explicitly so alpha compositing does not depend on a
        # draw adapter exposing its backing PIL image.
        target = frame if frame is not None else getattr(draw, "_image", None)
        if target is None:
            raise TypeError("SpeechBubble.draw requires its RGBA frame")
        target.alpha_composite(card, (x1 - bleed, y1 - bleed))
