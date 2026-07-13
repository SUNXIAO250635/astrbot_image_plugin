from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


@dataclass(slots=True)
class SplitResult:
    method: str
    paths: list[str]
    reason: str = ""


class SmartMemeSplitter:
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def split(
        self,
        image_path: str,
        output_dir: str,
        *,
        vision_boxes: list | None = None,
        stem: str = "meme",
    ) -> SplitResult:
        os.makedirs(output_dir, exist_ok=True)
        if _as_bool(self.config.get("adaptive_enabled", True), True):
            boxes, mask = self._adaptive_boxes(image_path)
            if self._valid_boxes(boxes):
                paths = self._save_boxes(image_path, boxes, output_dir, stem, mask=mask)
                if paths:
                    return SplitResult("adaptive_outline", paths)

        rows = max(0, _as_int(self.config.get("grid_rows"), 0))
        cols = max(0, _as_int(self.config.get("grid_columns"), 0))
        if rows and cols:
            boxes = self._grid_boxes(image_path, rows, cols)
            paths = self._save_boxes(image_path, boxes, output_dir, stem)
            if paths:
                return SplitResult("manual_grid", paths)

        if vision_boxes:
            boxes = self._normalize_vision_boxes(image_path, vision_boxes)
            if self._valid_boxes(boxes):
                paths = self._save_boxes(image_path, boxes, output_dir, stem)
                if paths:
                    return SplitResult("vision_boxes", paths)

        return SplitResult("original", [image_path], "未识别到可靠切片")

    def _adaptive_boxes(self, image_path: str):
        image = Image.open(image_path).convert("RGBA")
        original_size = image.size
        analysis_max_dimension = max(
            1, _as_int(self.config.get("analysis_max_dimension"), 1024)
        )
        scale = min(1.0, analysis_max_dimension / max(image.size))
        if scale < 1:
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        background = _corner_background(image)
        tolerance = max(5, _as_int(self.config.get("background_tolerance"), 36))
        outline = max(0, min(255, _as_int(self.config.get("outline_threshold"), 90)))
        alpha_values = image.getchannel("A")
        has_transparency = ImageStat.Stat(alpha_values).extrema[0][0] < 245
        red, green, blue, _ = image.split()
        background_image = Image.new("RGB", image.size, background)
        difference = ImageChops.difference(image.convert("RGB"), background_image)
        difference_red, difference_green, difference_blue = difference.split()
        maximum_difference = ImageChops.lighter(
            ImageChops.lighter(difference_red, difference_green),
            difference_blue,
        )
        distant_from_background = maximum_difference.point(
            lambda value: 255 if value >= tolerance else 0
        )
        maximum_color = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        dark_outline = maximum_color.point(lambda value: 255 if value <= outline else 0)
        foreground = ImageChops.lighter(distant_from_background, dark_outline)
        if has_transparency:
            partially_transparent = ImageOps.invert(alpha_values).point(
                lambda value: 255 if value > 5 else 0
            )
            foreground = ImageChops.lighter(foreground, partially_transparent)
        transparent_or_near = alpha_values.point(
            lambda value: 255 if value <= 12 else 0
        )
        mask = ImageChops.darker(foreground, ImageOps.invert(transparent_or_near))
        dilation = _odd(_as_int(self.config.get("connect_radius"), 9), 3, 31)
        mask = mask.filter(ImageFilter.MaxFilter(dilation))
        mask = mask.filter(ImageFilter.MinFilter(_odd(max(3, dilation // 2), 3, 15)))
        boxes = _connected_boxes(
            mask,
            max(
                16,
                int(
                    image.width
                    * image.height
                    * _as_float(self.config.get("min_area_ratio"), 0.003)
                ),
            ),
        )
        padding = max(0, _as_int(self.config.get("padding"), 12))
        scaled_boxes = []
        for left, top, right, bottom in boxes:
            if scale < 1:
                left, top, right, bottom = (
                    int(left / scale),
                    int(top / scale),
                    int(right / scale),
                    int(bottom / scale),
                )
            scaled_boxes.append(
                (
                    max(0, left - padding),
                    max(0, top - padding),
                    min(original_size[0], right + padding),
                    min(original_size[1], bottom + padding),
                )
            )
        original_mask = mask.resize(original_size, Image.Resampling.NEAREST)
        return _merge_boxes(scaled_boxes), original_mask

    def _grid_boxes(self, image_path: str, rows: int, cols: int):
        with Image.open(image_path) as image:
            width, height = image.size
        margin = max(0, _as_int(self.config.get("grid_margin"), 0))
        gap = max(0, _as_int(self.config.get("grid_gap"), 0))
        usable_width = width - margin * 2 - gap * (cols - 1)
        usable_height = height - margin * 2 - gap * (rows - 1)
        if usable_width <= 0 or usable_height <= 0:
            return []
        cell_width = usable_width / cols
        cell_height = usable_height / rows
        return [
            (
                round(margin + col * (cell_width + gap)),
                round(margin + row * (cell_height + gap)),
                round(margin + col * (cell_width + gap) + cell_width),
                round(margin + row * (cell_height + gap) + cell_height),
            )
            for row in range(rows)
            for col in range(cols)
        ]

    def _normalize_vision_boxes(self, image_path: str, values: list):
        with Image.open(image_path) as image:
            width, height = image.size
        boxes = []
        for value in values:
            if isinstance(value, dict):
                raw = [
                    value.get("x", value.get("left")),
                    value.get("y", value.get("top")),
                    value.get("width", value.get("right")),
                    value.get("height", value.get("bottom")),
                ]
                if "right" in value:
                    raw[2] = float(value["right"]) - float(raw[0])
                if "bottom" in value:
                    raw[3] = float(value["bottom"]) - float(raw[1])
            elif isinstance(value, (list, tuple)) and len(value) >= 4:
                raw = list(value[:4])
            else:
                continue
            try:
                x, y, box_width, box_height = map(float, raw)
            except (TypeError, ValueError):
                continue
            if max(x, y, box_width, box_height) <= 1.0:
                x, box_width = x * width, box_width * width
                y, box_height = y * height, box_height * height
            boxes.append(
                (
                    max(0, round(x)),
                    max(0, round(y)),
                    min(width, round(x + box_width)),
                    min(height, round(y + box_height)),
                )
            )
        return boxes

    def _valid_boxes(self, boxes: list[tuple[int, int, int, int]]) -> bool:
        minimum = max(1, _as_int(self.config.get("minimum_slices"), 2))
        expected = max(0, _as_int(self.config.get("expected_slices"), 0))
        if len(boxes) < minimum or (expected and len(boxes) != expected):
            return False
        for index, box in enumerate(boxes):
            left, top, right, bottom = box
            if right - left < 16 or bottom - top < 16:
                return False
            for other in boxes[index + 1 :]:
                if _intersection_over_union(box, other) > 0.5:
                    return False
        return True

    def _save_boxes(
        self,
        image_path: str,
        boxes: list,
        output_dir: str,
        stem: str,
        *,
        mask: Image.Image | None = None,
    ) -> list[str]:
        paths = []
        transparent = _as_bool(self.config.get("transparent_background", True), True)
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
            for index, box in enumerate(boxes, start=1):
                crop = image.crop(box)
                if transparent and mask is not None:
                    crop_mask = mask.crop(box)
                    original_alpha = crop.getchannel("A")
                    transparent_alpha = Image.new("L", crop.size, 0)
                    crop.putalpha(
                        Image.composite(
                            original_alpha,
                            transparent_alpha,
                            crop_mask,
                        )
                    )
                path = os.path.join(output_dir, f"{stem}_{index}.png")
                crop.save(path, "PNG")
                paths.append(os.path.abspath(path))
        return paths


def _connected_boxes(mask: Image.Image, minimum_area: int):
    width, height = mask.size
    pixels = mask.load()
    visited = bytearray(width * height)
    boxes = []
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if visited[offset] or pixels[x, y] == 0:
                continue
            queue = deque([(x, y)])
            visited[offset] = 1
            count = 0
            left = right = x
            top = bottom = y
            while queue:
                current_x, current_y = queue.popleft()
                count += 1
                left, right = min(left, current_x), max(right, current_x)
                top, bottom = min(top, current_y), max(bottom, current_y)
                for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for next_x in range(
                        max(0, current_x - 1), min(width, current_x + 2)
                    ):
                        next_offset = next_y * width + next_x
                        if visited[next_offset] or pixels[next_x, next_y] == 0:
                            continue
                        visited[next_offset] = 1
                        queue.append((next_x, next_y))
            if count >= minimum_area:
                boxes.append((left, top, right + 1, bottom + 1))
    return boxes


def _merge_boxes(boxes: list[tuple[int, int, int, int]]):
    result = []
    for box in sorted(boxes, key=lambda value: (value[1], value[0])):
        merged = False
        for index, existing in enumerate(result):
            if _intersection_over_union(box, existing) > 0.15:
                result[index] = (
                    min(box[0], existing[0]),
                    min(box[1], existing[1]),
                    max(box[2], existing[2]),
                    max(box[3], existing[3]),
                )
                merged = True
                break
        if not merged:
            result.append(box)
    return sorted(result, key=lambda value: (value[1], value[0]))


def _intersection_over_union(first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if not intersection:
        return 0.0
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(1, first_area + second_area - intersection)


def _corner_background(image: Image.Image):
    points = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    return tuple(
        sorted(point[channel] for point in points)[len(points) // 2]
        for channel in range(3)
    )


def _odd(value: int, minimum: int, maximum: int):
    value = max(minimum, min(maximum, value))
    return value if value % 2 else value + 1


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "启用"}


def _as_int(value, default=0):
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default
