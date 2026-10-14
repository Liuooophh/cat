"""Split the supplied transparent pose sheets into the pet's frame folders."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "assets" / "source"
OUTPUT = ROOT / "assets" / "pet"


def largest_component_bbox(tile: Image.Image, threshold: int = 48) -> tuple[int, int, int, int] | None:
    """Find the largest alpha-connected object using row runs (labels stay out)."""
    alpha = tile.getchannel("A").tobytes()
    width, height = tile.size
    parent: list[int] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for y in range(height):
        offset = y * width
        row_runs: list[tuple[int, int, int]] = []
        x = 0
        while x < width:
            while x < width and alpha[offset + x] < threshold:
                x += 1
            if x == width:
                break
            start = x
            while x < width and alpha[offset + x] >= threshold:
                x += 1
            end = x - 1
            ident = len(parent)
            parent.append(ident)
            runs.append((y, start, end, ident))
            row_runs.append((start, end, ident))

        pi = 0
        for start, end, ident in row_runs:
            while pi < len(previous) and previous[pi][1] < start - 1:
                pi += 1
            pj = pi
            while pj < len(previous) and previous[pj][0] <= end + 1:
                union(ident, previous[pj][2])
                pj += 1
        previous = row_runs

    if not runs:
        return None
    components: dict[int, list[int]] = {}
    for y, x1, x2, ident in runs:
        root = find(ident)
        box = components.get(root)
        if box is None:
            components[root] = [x1, y, x2 + 1, y + 1, x2 - x1 + 1]
        else:
            box[0] = min(box[0], x1)
            box[1] = min(box[1], y)
            box[2] = max(box[2], x2 + 1)
            box[3] = max(box[3], y + 1)
            box[4] += x2 - x1 + 1
    best = max(components.values(), key=lambda item: item[4])
    return best[0], best[1], best[2], best[3]


def all_components(tile: Image.Image, threshold: int = 48) -> list[tuple[tuple[int, int, int, int], int]]:
    """Return component bounds and pixel area for inspecting sheet layouts."""
    alpha = tile.getchannel("A").tobytes()
    width, height = tile.size
    parent: list[int] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for y in range(height):
        offset = y * width
        row_runs: list[tuple[int, int, int]] = []
        x = 0
        while x < width:
            while x < width and alpha[offset + x] < threshold:
                x += 1
            if x == width:
                break
            start = x
            while x < width and alpha[offset + x] >= threshold:
                x += 1
            end = x - 1
            ident = len(parent)
            parent.append(ident)
            runs.append((y, start, end, ident))
            row_runs.append((start, end, ident))
        pi = 0
        for start, end, ident in row_runs:
            while pi < len(previous) and previous[pi][1] < start - 1:
                pi += 1
            pj = pi
            while pj < len(previous) and previous[pj][0] <= end + 1:
                union(ident, previous[pj][2])
                pj += 1
        previous = row_runs

    result: dict[int, list[int]] = {}
    for y, x1, x2, ident in runs:
        root = find(ident)
        box = result.get(root)
        if box is None:
            result[root] = [x1, y, x2 + 1, y + 1, x2 - x1 + 1]
        else:
            box[0] = min(box[0], x1)
            box[1] = min(box[1], y)
            box[2] = max(box[2], x2 + 1)
            box[3] = max(box[3], y + 1)
            box[4] += x2 - x1 + 1
    return [((b[0], b[1], b[2], b[3]), b[4]) for b in result.values()]


def baseline_artifacts(tile: Image.Image) -> list[tuple[int, int, int, int]]:
    """Find thin, detached horizontal remnants below a character's feet."""
    components = all_components(tile, threshold=48)
    if len(components) < 2:
        return []

    body_box, _ = max(components, key=lambda item: item[1])
    body_bottom = body_box[3]
    min_width = max(40, int(tile.width * 0.22))
    max_height = max(6, int(tile.height * 0.05))
    min_y = int(tile.height * 0.78)
    artifacts = []
    for box, area in components:
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        if (top >= body_bottom - 1 and top >= min_y
                and width >= min_width and height <= max_height):
            artifacts.append(box)
    return artifacts


def remove_baseline_artifacts(tile: Image.Image) -> Image.Image:
    """Clear detached ground lines while preserving the character and shadow."""
    cleaned = tile.copy()
    components = all_components(tile, threshold=48)
    if not components:
        return cleaned
    body_box, _ = max(components, key=lambda item: item[1])
    body_bottom = body_box[3]
    for box in baseline_artifacts(tile):
        left, top, right, bottom = box
        erase = (
            max(0, left - 3),
            max(body_bottom, top - 1),
            min(tile.width, right + 3),
            min(tile.height, bottom + 2),
        )
        cleaned.paste((0, 0, 0, 0), erase)
    return cleaned


def include_nearby_yellow_accents(
        sheet: Image.Image,
        components: list[tuple[tuple[int, int, int, int], int]],
        body_box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Expand a pose crop to keep its detached yellow emphasis marks."""
    left, top, right, bottom = body_box
    center_x = (left + right) / 2
    max_x_distance = max(44, (right - left) * 0.56)
    pixels = sheet.load()
    for box, area in components:
        if box == body_box or area > 1800:
            continue
        x1, y1, x2, y2 = box
        component_center_x = (x1 + x2) / 2
        if abs(component_center_x - center_x) > max_x_distance:
            continue
        if y1 < top - 100 or y1 > top + 22 or y2 > top + 46:
            continue

        yellow_pixels = 0
        visible_pixels = 0
        for y in range(y1, y2):
            for x in range(x1, x2):
                red, green, blue, alpha = pixels[x, y]
                if alpha < 48:
                    continue
                visible_pixels += 1
                if (red >= 140 and green >= 75 and blue < 145
                        and red > green * 1.04 and green > blue * 1.25):
                    yellow_pixels += 1
        if yellow_pixels < 6 or yellow_pixels / max(visible_pixels, 1) < 0.32:
            continue
        left, top = min(left, x1), min(top, y1)
        right, bottom = max(right, x2), max(bottom, y2)
    return left, top, right, bottom


def save(sheet: Image.Image,
         components: list[tuple[tuple[int, int, int, int], int]],
         center_x: int, y1: int, y2: int, state: str, index: int) -> None:
    candidates = []
    for box, area in components:
        left, top, right, bottom = box
        if bottom - top < 85 or right - left < 100 or area < 3500:
            continue
        center_y = (top + bottom) / 2
        if y1 <= center_y < y2:
            candidates.append((abs((left + right) / 2 - center_x), box))
    if not candidates:
        raise ValueError(f"No character found near x={center_x}, y={y1}..{y2}")
    _, box = min(candidates, key=lambda item: item[0])
    left, top, right, bottom = include_nearby_yellow_accents(sheet, components, box)
    pad = 3
    bbox = (max(0, left - pad), max(0, top - pad),
            min(sheet.width, right + pad), min(sheet.height, bottom + pad))
    image = remove_baseline_artifacts(sheet.crop(bbox))
    target = OUTPUT / state / f"{index:02d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, optimize=True)


def main() -> None:
    with Image.open(SOURCES / "bear_actions_1.png") as original:
        first = original.convert("RGBA")
    with Image.open(SOURCES / "bear_actions_2.png") as original:
        second = original.convert("RGBA")

    first_components = all_components(first)
    second_components = all_components(second)
    if __import__("sys").argv[1:] == ["inspect"]:
        for name, comps in (("sheet1", first_components), ("sheet2", second_components)):
            comps = [(box, area) for box, area in comps
                     if box[3] - box[1] > 85 and area > 3500]
            print(name, *sorted(comps, key=lambda item: (item[0][1], item[0][0])), sep="\n")
        return

    for existing in OUTPUT.glob("*/*.png"):
        existing.unlink()

    # Idle / blink, plus the first sheet's eight evenly spaced walking frames.
    save(second, second_components, 117, 0, 260, "idle", 1)
    save(second, second_components, 338, 0, 260, "blink", 1)
    for index, center in enumerate([102, 286, 468, 656, 856, 1054, 1250, 1444], 1):
        save(first, first_components, center, 260, 515, "walk", index)

    for state, center in [("happy", 562), ("laugh", 781), ("angry", 998),
                          ("confused", 1216), ("surprised", 1422)]:
        save(second, second_components, center, 0, 260, state, 1)
    for state, center in [("sleep", 136), ("sleep", 394), ("sleep", 652),
                          ("yawn", 923), ("interaction", 1431)]:
        save(second, second_components, center, 520, 740, state,
             len(list((OUTPUT / state).glob("*.png"))) + 1)
    for state, center in [("clicked", 122), ("clicked", 333), ("clicked", 551),
                          ("clicked", 775), ("clicked", 1005), ("wave", 1194)]:
        save(second, second_components, center, 745, 1024, state,
             len(list((OUTPUT / state).glob("*.png"))) + 1)

    # Extra poses from the first sheet enrich actions using the same folders.
    for state, center in [("happy", 122), ("angry", 353), ("laugh", 584),
                          ("yawn", 789), ("surprised", 1007),
                          ("confused", 1214), ("idle", 1424)]:
        save(first, first_components, center, 520, 770, state,
             len(list((OUTPUT / state).glob("*.png"))) + 1)
    for state, center in [("clicked", 110), ("clicked", 318),
                          ("interaction", 510), ("wave", 689),
                          ("sleep", 892), ("sleep", 1136), ("sleep", 1396)]:
        save(first, first_components, center, 775, 1024, state,
             len(list((OUTPUT / state).glob("*.png"))) + 1)

    for state_dir in sorted(path for path in OUTPUT.iterdir() if path.is_dir()):
        frames = sorted(state_dir.glob("*.png"))
        if not frames:
            continue
        print(f"{state_dir.name}: {len(frames)} frame(s)")


if __name__ == "__main__":
    main()
