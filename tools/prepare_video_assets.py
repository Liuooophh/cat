"""Convert the five studio-background cat clips into runtime MP4 assets.

Each output frame stores straight RGB in its left half and a grayscale alpha
mask in its right half.  The desktop app can therefore keep using its existing
per-pixel-alpha window while distributing MP4 files instead of PNG sequences.

This is a development tool.  OpenCV, NumPy and SciPy are intentionally not
runtime dependencies of the packaged pet.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi


CELL = (320, 300)
GROUND_Y = 282
SOURCE_SCALE = 0.32
SOURCE_GROUND_Y = 805
FPS = 24.0


@dataclass(frozen=True)
class SourceClip:
    state: str
    filename: str
    frame_count: int
    loop: bool
    stages: dict[str, tuple[int, int]] | None = None
    loop_blend_frames: int = 0


CLIPS = (
    SourceClip("idle", "cat-idle-final.mp4", 240, True, loop_blend_frames=4),
    SourceClip("click", "cat-click-final.mp4", 96, False),
    SourceClip("eat", "cat-eat-final.mp4", 240, False),
    SourceClip("groom", "cat-groom-final.mp4", 240, False),
    SourceClip(
        "sleep",
        "cat-sleep-final.mp4",
        240,
        True,
        {"enter": (0, 144), "loop": (144, 168), "exit": (168, 240)},
        3,
    ),
)


def _read_exact(stream: object, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def remove_white(rgb: np.ndarray, *, floor_threshold: int = 195) -> tuple[np.ndarray, np.ndarray]:
    """Return decontaminated color and alpha for a white studio background."""
    color = rgb.astype("float32")
    neutral = np.ptp(rgb.astype("int16"), axis=2) <= 12
    minimum = rgb.min(axis=2)
    candidate = (minimum >= 232) & neutral
    # The studio floor becomes light gray under the paws and tail. A white-only
    # key incorrectly keeps this contact shadow as an opaque white pedestal.
    # Fade the threshold through the paw zone. A hard horizontal threshold
    # change left a triangular floor fragment beside the rear paw and introduced
    # a visible straight edge. Keep the chest/face threshold unchanged.
    row_threshold = np.interp(
        np.arange(rgb.shape[0]),
        [SOURCE_GROUND_Y - 85, SOURCE_GROUND_Y - 40],
        [232, floor_threshold],
    )[:, None]
    candidate |= (minimum >= row_threshold) & neutral
    candidate = candidate.astype("uint8")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    border_ids = np.unique(
        np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
    )
    background_ids = {int(value) for value in border_ids if value}
    for label in range(1, count):
        if label not in background_ids and stats[label, cv2.CC_STAT_AREA] >= 30:
            region = labels == label
            if float(color[region].mean()) >= 245.5:
                background_ids.add(label)
    background = np.isin(labels, list(background_ids))
    foreground = ~background

    # Keep the cat plus nearby action props such as the food.  Tiny isolated
    # compression flecks are discarded without requiring the prop to touch fur.
    component_count, components, component_stats, _ = cv2.connectedComponentsWithStats(
        foreground.astype("uint8"), 8
    )
    if component_count > 1:
        main_label = 1 + int(component_stats[1:, cv2.CC_STAT_AREA].argmax())
        main = component_stats[main_label]
        margin = 70
        left = max(0, int(main[cv2.CC_STAT_LEFT]) - margin)
        top = max(0, int(main[cv2.CC_STAT_TOP]) - margin)
        right = min(rgb.shape[1], left + int(main[cv2.CC_STAT_WIDTH]) + margin * 2)
        bottom = min(rgb.shape[0], top + int(main[cv2.CC_STAT_HEIGHT]) + margin * 2)
        keep = components == main_label
        for label in range(1, component_count):
            if label == main_label:
                continue
            item = component_stats[label]
            area = int(item[cv2.CC_STAT_AREA])
            x = int(item[cv2.CC_STAT_LEFT])
            y = int(item[cv2.CC_STAT_TOP])
            width = int(item[cv2.CC_STAT_WIDTH])
            height = int(item[cv2.CC_STAT_HEIGHT])
            near_actor = x + width >= left and x < right and y + height >= top and y < bottom
            if area >= 120 or (area >= 20 and near_actor):
                keep |= components == label
        foreground = keep

    inner = ndi.binary_erosion(foreground, iterations=4, border_value=0)
    outer = ndi.binary_dilation(foreground, iterations=5)
    band = outer & ~inner
    alpha = foreground.astype("float32")
    _, foreground_indices = ndi.distance_transform_edt(~inner, return_indices=True)
    opaque_color = color[tuple(foreground_indices)]
    clean_background = ~ndi.binary_dilation(foreground, iterations=8)
    _, background_indices = ndi.distance_transform_edt(
        ~clean_background, return_indices=True
    )
    background_color = np.clip(color[tuple(background_indices)], 247, 253)
    direction = background_color - opaque_color
    delta = background_color - color
    estimate = np.sum(delta * direction, axis=2) / np.maximum(
        np.sum(direction * direction, axis=2), 64
    )
    alpha[band] = np.clip(estimate[band], 0, 1)
    alpha[alpha < 0.018] = 0

    result = color.copy()
    visible = band & (alpha > 0.018)
    result[visible] = (
        color[visible] - (1 - alpha[visible, None]) * background_color[visible]
    ) / alpha[visible, None]
    result = np.clip(result, 0, 255)
    result[alpha == 0] = 0
    return result, alpha


def transform(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Place a premultiplied resize on the app's fixed 320x300 canvas."""
    source_height, source_width = alpha.shape
    scaled_size = (
        max(1, round(source_width * SOURCE_SCALE)),
        max(1, round(source_height * SOURCE_SCALE)),
    )
    resized_alpha = cv2.resize(alpha, scaled_size, interpolation=cv2.INTER_AREA)
    resized_premultiplied = cv2.resize(
        rgb * alpha[..., None], scaled_size, interpolation=cv2.INTER_AREA
    )
    resized_color = np.divide(
        resized_premultiplied,
        resized_alpha[..., None],
        out=np.zeros_like(resized_premultiplied),
        where=resized_alpha[..., None] > 0.0001,
    )
    color_u8 = np.clip(resized_color, 0, 255).astype("uint8")
    alpha_u8 = np.round(np.clip(resized_alpha, 0, 1) * 255).astype("uint8")

    canvas_color = np.zeros((CELL[1], CELL[0], 3), dtype="uint8")
    canvas_alpha = np.zeros((CELL[1], CELL[0]), dtype="uint8")
    target_x = round(CELL[0] / 2 - scaled_size[0] / 2)
    target_y = round(GROUND_Y - SOURCE_GROUND_Y * SOURCE_SCALE)
    src_left = max(0, -target_x)
    src_top = max(0, -target_y)
    src_right = min(scaled_size[0], CELL[0] - target_x)
    src_bottom = min(scaled_size[1], CELL[1] - target_y)
    dst_left = max(0, target_x)
    dst_top = max(0, target_y)
    dst_right = dst_left + src_right - src_left
    dst_bottom = dst_top + src_bottom - src_top
    canvas_color[dst_top:dst_bottom, dst_left:dst_right] = color_u8[
        src_top:src_bottom, src_left:src_right
    ]
    canvas_alpha[dst_top:dst_bottom, dst_left:dst_right] = alpha_u8[
        src_top:src_bottom, src_left:src_right
    ]

    packed = np.zeros((CELL[1], CELL[0] * 2, 3), dtype="uint8")
    packed[:, : CELL[0]] = canvas_color
    packed[:, CELL[0] :] = canvas_alpha[..., None]
    return packed


def convert_clip(
    clip: SourceClip,
    input_dir: Path,
    output_dir: Path,
    ffmpeg: Path,
    crf: int,
) -> tuple[str, int]:
    source = input_dir / clip.filename
    target = output_dir / f"{clip.state}.mp4"
    frame_size = 1088 * 816 * 3
    decoder = subprocess.Popen(
        [
            str(ffmpeg), "-v", "error", "-i", str(source), "-an",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encoder = subprocess.Popen(
        [
            str(ffmpeg), "-y", "-v", "error", "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-s", f"{CELL[0] * 2}x{CELL[1]}",
            "-r", str(int(FPS)), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "medium", "-crf", str(crf), "-g", str(int(FPS)),
            "-keyint_min", str(int(FPS)), "-sc_threshold", "0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert decoder.stdout is not None and encoder.stdin is not None
    converted = 0
    try:
        while True:
            raw = _read_exact(decoder.stdout, frame_size)
            if not raw:
                break
            if len(raw) != frame_size:
                raise RuntimeError(
                    f"{clip.filename} ended with a partial raw frame ({len(raw)} bytes)"
                )
            frame = np.frombuffer(raw, dtype="uint8").reshape(816, 1088, 3)
            color, alpha = remove_white(frame)
            encoder.stdin.write(transform(color, alpha).tobytes())
            converted += 1
            if converted % 24 == 0:
                print(f"{clip.state}: {converted}/{clip.frame_count}", flush=True)
    finally:
        decoder.stdout.close()
        encoder.stdin.close()
    decoder_error = decoder.stderr.read().decode("utf-8", "replace")
    encoder_error = encoder.stderr.read().decode("utf-8", "replace")
    decoder_code = decoder.wait()
    encoder_code = encoder.wait()
    if decoder_code or encoder_code:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg failed for {clip.state}: decoder={decoder_code} {decoder_error}; "
            f"encoder={encoder_code} {encoder_error}"
        )
    if converted != clip.frame_count:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"{clip.state} produced {converted} frames; expected {clip.frame_count}"
        )
    return clip.state, converted


def build_manifest() -> dict[str, object]:
    clips: dict[str, object] = {}
    for clip in CLIPS:
        metadata: dict[str, object] = {
            "file": f"{clip.state}.mp4",
            "fps": FPS,
            "loop": clip.loop,
            "source_facing": "front",
            "frame_count": clip.frame_count,
            "play_range": [0, clip.frame_count],
            "idle_entry_frame": 0,
        }
        if clip.stages:
            metadata["stages"] = {
                name: list(bounds) for name, bounds in clip.stages.items()
            }
        if clip.loop_blend_frames:
            metadata["loop_blend_frames"] = clip.loop_blend_frames
        clips[clip.state] = metadata
    return {
        "format_version": 3,
        "asset_format": "mp4-color-alpha",
        "video_layout": "color-alpha-side-by-side",
        "cell_size": list(CELL),
        "ground_y": GROUND_Y,
        "fps": FPS,
        "source_size": [1088, 816],
        "source_scale": SOURCE_SCALE,
        "audio": False,
        "matte_revision": "neutral-contact-floor-v3-smooth-paw",
        "clips": clips,
    }


def build_contact_sheet(output_dir: Path, target: Path, ffmpeg: Path) -> None:
    """Render sampled RGBA frames over light/dark backgrounds for visual QA."""
    columns = 6
    row_height = CELL[1] + 28
    sheet = Image.new("RGB", (CELL[0] * columns, row_height * len(CLIPS)), "#dadddf")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    packed_size = CELL[0] * 2 * CELL[1] * 3
    for row, clip in enumerate(CLIPS):
        requested = {
            round(index * (clip.frame_count - 1) / (columns - 1)): index
            for index in range(columns)
        }
        decoder = subprocess.Popen(
            [
                str(ffmpeg), "-v", "error", "-i", str(output_dir / f"{clip.state}.mp4"),
                "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert decoder.stdout is not None
        index = 0
        while True:
            raw = _read_exact(decoder.stdout, packed_size)
            if not raw:
                break
            if index in requested:
                packed = np.frombuffer(raw, dtype="uint8").reshape(
                    CELL[1], CELL[0] * 2, 3
                )
                rgba = np.dstack(
                    (packed[:, : CELL[0]], packed[:, CELL[0] :, 0])
                )
                sprite = Image.fromarray(rgba, "RGBA")
                panel = Image.new("RGBA", CELL, (31, 36, 45, 255))
                ImageDraw.Draw(panel).rectangle(
                    (CELL[0] // 2, 0, CELL[0], CELL[1]), fill=(239, 242, 245, 255)
                )
                panel.alpha_composite(sprite)
                column = requested[index]
                x, y = column * CELL[0], row * row_height + 28
                sheet.paste(panel.convert("RGB"), (x, y))
                draw.text(
                    (x + 8, row * row_height + 6),
                    f"{clip.state}  frame {index + 1}/{clip.frame_count}",
                    fill=(24, 28, 35),
                    font=font,
                )
            index += 1
        decoder.stdout.close()
        error = decoder.stderr.read().decode("utf-8", "replace")
        if decoder.wait() != 0:
            raise RuntimeError(f"Unable to decode QA frames for {clip.state}: {error}")
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=95)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    default_ffmpeg = next(
        (root.parent / "media-tools").rglob("ffmpeg*.exe"),
        Path(shutil.which("ffmpeg") or "ffmpeg"),
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=default_ffmpeg)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--crf", type=int, default=12)
    parser.add_argument("--only", choices=[clip.state for clip in CLIPS])
    parser.add_argument("--qa-output", type=Path)
    args = parser.parse_args()
    if not 0 <= args.crf <= 51:
        raise SystemExit("--crf must be between 0 and 51")
    selected = [clip for clip in CLIPS if not args.only or clip.state == args.only]
    missing = [str(args.input_dir / clip.filename) for clip in selected
               if not (args.input_dir / clip.filename).is_file()]
    if missing:
        raise SystemExit("Missing source clips:\n" + "\n".join(missing))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(args.workers), len(selected)))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                convert_clip, clip, args.input_dir, args.output_dir, args.ffmpeg, args.crf
            ): clip.state
            for clip in selected
        }
        for future in as_completed(futures):
            state, count = future.result()
            print(f"saved {state}.mp4 ({count} frames)", flush=True)
    if not args.only:
        manifest = build_manifest()
        (args.output_dir / "clips.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.qa_output:
            build_contact_sheet(args.output_dir, args.qa_output, args.ffmpeg)


if __name__ == "__main__":
    main()
