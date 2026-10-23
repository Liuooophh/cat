"""Small local sleep effects on the fixed sprite and application canvases."""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont


BREATH_PERIOD = 3.8


@lru_cache(maxsize=16)
def _breath_grid(width: int, height: int):
    """A fixed mesh whose edges are pinned, so the head and paws never move."""
    columns, rows = 8, 6
    points = []
    for row in range(rows):
        top, bottom = round(row * height / rows), round((row + 1) * height / rows)
        for column in range(columns):
            left = round(column * width / columns)
            right = round((column + 1) * width / columns)
            quad = ((left, top), (left, bottom), (right, bottom), (right, top))
            weights = tuple(
                math.sin(math.pi * x / width) ** 2
                * math.sin(math.pi * y / height) ** 2
                for x, y in quad
            )
            points.append(((left, top, right, bottom), quad, weights))
    return tuple(points)


def sleep_breath(sprite: Image.Image, elapsed: float, mirror: bool = False) -> Image.Image:
    """Raise the upper abdomen by at most 1.6 px over a 3.8 second breath.

    Only a region behind the shoulder and above the feet is resampled. The
    original canvas size, head, tail tip, paws and contact line stay untouched.
    Cached source frames are never mutated, and alpha edges stay premultiplied.
    """
    bounds = sprite.getchannel("A").getbbox()
    if not bounds:
        return sprite
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if width < 20 or height < 20:
        return sprite
    first, last = ((0.10, 0.66) if mirror else (0.34, 0.90))
    region = (
        max(0, round(left + width * first)), max(0, top - 8),
        min(sprite.width, round(left + width * last)),
        min(sprite.height, round(top + height * 0.78)),
    )
    patch = sprite.crop(region).convert("RGBa")
    # Begin at an exhale and ease at both ends; do not jerk on loop entry.
    amplitude = 1.6 * min(sprite.width / 320, sprite.height / 300)
    amplitude *= 0.5 - 0.5 * math.cos(2 * math.pi * max(0.0, elapsed) / BREATH_PERIOD)
    if amplitude < 0.01:
        return sprite
    mesh = []
    for rectangle, quad, weights in _breath_grid(*patch.size):
        source = tuple(value for ((x, y), weight) in zip(quad, weights)
                       for value in (x, y + amplitude * weight))
        mesh.append((rectangle, source))
    patch = patch.transform(patch.size, Image.Transform.MESH, mesh,
                            resample=Image.Resampling.BICUBIC).convert("RGBA")
    result = sprite.copy()
    result.paste(patch, region[:2])
    return result


@lru_cache(maxsize=12)
def _zzz_font(size: int) -> ImageFont.ImageFont:
    for name in (r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
                 r"C:\Windows\Fonts\segoeui.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_sleep_zzz(frame: Image.Image, sprite_bbox: tuple[int, int, int, int],
                   elapsed: float, mirror: bool = False) -> None:
    """Draw three legible sleep marks with a gentle, continuously visible drift.

    A dark semibold glyph and a pale halo remain readable on both light and
    dark desktops. The cluster stays below the sleeping information card and
    above the lower head, moving less than two pixels rather than disappearing.
    """
    left, top, right, bottom = sprite_bbox
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return
    head_x = right - width * 0.20 if mirror else left + width * 0.20
    baseline = top + height * 0.14
    direction = -1 if mirror else 1
    phase = max(0.0, elapsed) * 2 * math.pi / 4.8
    letters = []
    for index, size in enumerate((16, 20, 24)):
        wave = math.sin(phase - index * 0.65)
        opacity = round(229 + 14 * wave)
        x = min(frame.width - size - 5, max(5, head_x + direction * index * 17))
        y = max(5, baseline - index * 14 + wave * 1.5)
        letters.append((x, y, size, opacity))
    margin = 5
    first_x = max(0, math.floor(min(item[0] for item in letters)) - margin)
    first_y = max(0, math.floor(min(item[1] for item in letters)) - margin)
    last_x = min(frame.width, math.ceil(max(item[0] + item[2] for item in letters)) + margin)
    last_y = min(frame.height, math.ceil(max(item[1] + item[2] for item in letters)) + margin)
    if last_x <= first_x or last_y <= first_y:
        return
    quality = 3
    layer = Image.new("RGBA", ((last_x - first_x) * quality, (last_y - first_y) * quality))
    draw = ImageDraw.Draw(layer)
    for x, y, size, opacity in letters:
        position = round((x - first_x) * quality), round((y - first_y) * quality)
        font = _zzz_font(size * quality)
        draw.text((position[0], position[1] + quality), "z", font=font, anchor="lt",
                  fill=(27, 37, 54, round(opacity * 0.18)), stroke_width=2 * quality,
                  stroke_fill=(27, 37, 54, round(opacity * 0.18)))
        draw.text(position, "z", font=font, anchor="lt", fill=(64, 79, 103, opacity),
                  stroke_width=2 * quality, stroke_fill=(251, 253, 255, opacity))
    layer = layer.convert("RGBa").resize((last_x - first_x, last_y - first_y),
                                          Image.Resampling.LANCZOS).convert("RGBA")
    frame.alpha_composite(layer, (first_x, first_y))
