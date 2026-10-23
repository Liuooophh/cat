"""Validate and import the approved cat-assets.zip without changing its pixels."""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

from PIL import Image


def import_package(package: Path, target: Path) -> None:
    states = ("idle", "walk", "groom", "sleep")
    expected = {f"cat/{state}/{number:04d}.png"
                for state in states for number in range(1, 241)}
    with zipfile.ZipFile(package) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        if len(names) != len(set(names)) or set(names) != expected | {"cat/clips.json"}:
            raise ValueError("Expected exactly four 240-frame clips and cat/clips.json")
        source_manifest = json.loads(archive.read("cat/clips.json").decode("utf-8-sig"))
        if source_manifest.get("canvas") != {"width": 320, "height": 300}:
            raise ValueError("Expected a 320 x 300 canvas")
        # Validate everything before replacing any installed frames.
        frames = {}
        for name in sorted(expected):
            raw = archive.read(name)
            with Image.open(io.BytesIO(raw)) as image:
                if image.size != (320, 300) or image.mode != "RGBA":
                    raise ValueError(f"Unexpected frame format: {name}")
                image.verify()
            frames[name] = raw
    clips = {state: {"fps": 24, "loop": state != "groom",
                     "source_facing": "left" if state in {"walk", "sleep"} else "front",
                     "frame_count": 240, "play_range": [0, 240]}
             for state in states}
    clips["idle"]["loop_blend_frames"] = 3
    clips["walk"].update(stages={"enter": [0, 103], "loop": [103, 147], "exit": [147, 240]},
                         movement_range=[72, 160], loop_blend_frames=3)
    clips["groom"]["play_range"] = [0, 225]
    clips["sleep"].update(stages={"enter": [0, 132], "loop": [132, 178], "exit": [178, 240]},
                          loop_blend_frames=3)
    manifest = {"format_version": 2, "cell_size": [320, 300], "ground_y": 282,
                "fps": 24, "source_package": package.name, "clips": clips}
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    for state in states:
        (target / state).mkdir(exist_ok=True)
        if any(p.name not in {f"{i:04d}.png" for i in range(1, 241)}
               for p in (target / state).glob("*.png")):
            raise ValueError(f"Unexpected existing frames in {target / state}; move them aside first")
    for name, raw in frames.items():
        (target / Path(name).relative_to("cat")).write_bytes(raw)
    (target / "clips.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(frames)} unchanged RGBA frames to {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--target", type=Path,
                        default=Path(__file__).resolve().parents[1] / "assets/cat")
    args = parser.parse_args()
    import_package(args.package, args.target)
