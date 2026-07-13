from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import main
import imagegen_core.meme as meme_module
from imagegen_core.meme import SmartMemeSplitter
from imagegen_core.models import CallerContext, GenerationResult, MediaArtifact
from media import extract_all_media
from tests.fakes.runtime import FakeContext, plugin_config


def _sticker_sheet(
    path: Path,
    *,
    width: int = 360,
    height: int = 180,
    inset: int = 0,
    color_a=(235, 70, 80, 255),
    color_b=(50, 145, 235, 255),
) -> Path:
    image = Image.new("RGBA", (width, height), "white")
    draw = ImageDraw.Draw(image)
    margin = max(14, min(width, height) // 12) + inset
    gap = max(28, width // 10)
    sticker_width = (width - margin * 2 - gap) // 2
    top = margin
    bottom = height - margin
    first = (margin, top, margin + sticker_width, bottom)
    second = (margin + sticker_width + gap, top, width - margin, bottom)
    for box, color in ((first, color_a), (second, color_b)):
        draw.rounded_rectangle(box, radius=18, fill="black")
        inner = tuple(
            value + 7 if index < 2 else value - 7 for index, value in enumerate(box)
        )
        draw.rounded_rectangle(inner, radius=12, fill=color)
    image.save(path)
    return path


def _splitter(**overrides) -> SmartMemeSplitter:
    config = {
        "adaptive_enabled": True,
        "minimum_slices": 2,
        "background_tolerance": 30,
        "outline_threshold": 90,
        "connect_radius": 5,
        "min_area_ratio": 0.002,
        "padding": 4,
        "transparent_background": True,
    }
    config.update(overrides)
    return SmartMemeSplitter(config)


def test_adaptive_outline_splits_two_stickers_and_makes_background_transparent(
    tmp_path,
):
    source = _sticker_sheet(tmp_path / "sheet.png")

    result = _splitter().split(str(source), str(tmp_path / "out"))

    assert result.method == "adaptive_outline"
    assert len(result.paths) == 2
    with Image.open(result.paths[0]) as first:
        assert first.mode == "RGBA"
        assert first.getchannel("A").getextrema()[0] == 0


def test_adaptive_mask_uses_pillow_operations_instead_of_python_pixel_iteration(
    tmp_path, monkeypatch
):
    source = _sticker_sheet(tmp_path / "optimized.png")

    def reject_getdata(*args, **kwargs):
        raise AssertionError(
            "adaptive mask must not iterate over image pixels in Python"
        )

    monkeypatch.setattr(Image.Image, "getdata", reject_getdata)

    result = _splitter().split(str(source), str(tmp_path / "optimized_out"))

    assert result.method == "adaptive_outline"
    assert len(result.paths) == 2


def test_large_image_uses_configured_analysis_size_and_keeps_full_resolution_slices(
    tmp_path, monkeypatch
):
    source = _sticker_sheet(tmp_path / "large.png", width=3200, height=1600)
    analyzed_sizes = []
    connected_boxes = meme_module._connected_boxes

    def capture_analysis_size(mask, minimum_area):
        analyzed_sizes.append(mask.size)
        return connected_boxes(mask, minimum_area)

    monkeypatch.setattr(meme_module, "_connected_boxes", capture_analysis_size)

    result = _splitter(
        analysis_max_dimension=320,
        expected_slices=2,
    ).split(str(source), str(tmp_path / "large_out"))

    assert result.method == "adaptive_outline"
    assert len(result.paths) == 2
    assert analyzed_sizes == [(320, 160)]
    with Image.open(result.paths[0]) as first:
        assert first.width > 1000
        assert first.mode == "RGBA"
        assert first.getchannel("A").getextrema()[0] == 0


@pytest.mark.parametrize("case", range(30))
def test_adaptive_outline_is_stable_across_synthetic_variations(tmp_path, case):
    width = 300 + (case % 6) * 18
    height = 150 + (case % 5) * 12
    color_a = (170 + case % 70, 40 + case % 40, 55 + case % 35, 255)
    color_b = (35 + case % 45, 105 + case % 70, 170 + case % 75, 255)
    source = _sticker_sheet(
        tmp_path / f"sheet_{case}.png",
        width=width,
        height=height,
        color_a=color_a,
        color_b=color_b,
    )

    result = _splitter(expected_slices=2).split(
        str(source), str(tmp_path / f"out_{case}")
    )

    assert result.method == "adaptive_outline"
    assert len(result.paths) == 2


def test_manual_grid_fallback_splits_four_cells(tmp_path):
    source = tmp_path / "grid.png"
    Image.new("RGB", (200, 160), "white").save(source)
    splitter = _splitter(
        adaptive_enabled=False,
        grid_rows=2,
        grid_columns=2,
        grid_margin=10,
        grid_gap=4,
    )

    result = splitter.split(str(source), str(tmp_path / "grid_out"))

    assert result.method == "manual_grid"
    assert len(result.paths) == 4


def test_vision_boxes_are_used_after_local_methods_fail(tmp_path):
    source = tmp_path / "vision.png"
    Image.new("RGB", (200, 100), "white").save(source)
    boxes = [
        {"x": 0.05, "y": 0.1, "width": 0.4, "height": 0.8},
        {"x": 0.55, "y": 0.1, "width": 0.4, "height": 0.8},
    ]

    result = _splitter(adaptive_enabled=False).split(
        str(source), str(tmp_path / "vision_out"), vision_boxes=boxes
    )

    assert result.method == "vision_boxes"
    assert len(result.paths) == 2


def test_blank_image_falls_back_to_original(tmp_path):
    source = tmp_path / "blank.png"
    Image.new("RGB", (160, 100), "white").save(source)

    result = _splitter().split(str(source), str(tmp_path / "blank_out"))

    assert result.method == "original"
    assert result.paths == [str(source)]


def test_generation_response_parser_accepts_existing_local_paths(tmp_path):
    image_path = tmp_path / "local.png"
    video_path = tmp_path / "local.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")

    response = {
        "data": [
            {"url": str(image_path)},
            {"video_url": str(video_path)},
        ]
    }

    assert extract_all_media(response) == [
        ("image", str(image_path)),
        ("video", str(video_path)),
    ]


def test_meme_postprocessor_replaces_source_with_local_slices(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    managed_dir = tmp_path / "data" / "plugin_data"
    managed_dir.mkdir(parents=True)
    source = _sticker_sheet(managed_dir / "plugin_sheet.png")
    config = plugin_config()
    config["meme_splitter"] = {
        "enabled": True,
        "adaptive_enabled": True,
        "vision_enabled": False,
        "minimum_slices": 2,
        "expected_slices": 2,
        "connect_radius": 5,
        "padding": 4,
    }
    config["media"]["save_dir"] = "plugin_data"
    plugin = main.ImageGenPlugin(FakeContext(), config)
    plugin._meme_splitter = SmartMemeSplitter(config["meme_splitter"])
    result = GenerationResult(
        provider_id="test",
        media=[MediaArtifact("image", str(source), provider_id="test")],
    )

    processed = asyncio.run(plugin._postprocess_meme_result(result))

    assert len(processed.media) == 2
    assert all(item.temporary for item in processed.media)
    assert all(Path(item.value).exists() for item in processed.media)
    assert any("adaptive_outline" in warning for warning in processed.warnings)


def test_restored_background_meme_job_runs_postprocessor(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.chdir(tmp_path)
        managed_dir = tmp_path / "data" / "restored_data"
        managed_dir.mkdir(parents=True)
        source = _sticker_sheet(managed_dir / "restored_sheet.png")
        config = plugin_config()
        config["media"]["save_dir"] = "restored_data"
        config["meme_splitter"] = {
            "enabled": True,
            "adaptive_enabled": True,
            "vision_enabled": False,
            "minimum_slices": 2,
            "expected_slices": 2,
            "connect_radius": 5,
            "padding": 4,
        }
        context = FakeContext()
        plugin = main.ImageGenPlugin(context, config)
        result = GenerationResult(
            provider_id="test",
            media=[MediaArtifact("image", str(source), provider_id="test")],
        )
        caller = CallerContext(
            unified_msg_origin="test:private:user-1",
            sender_id="user-1",
        )

        await plugin._background_job_completed(
            "job-restored",
            result,
            None,
            {
                "task_name": "meme",
                "caller": caller.to_dict(),
                "handle": {"remote_task_id": "remote-1"},
                "postprocess": "meme",
            },
        )

        assert len(context.sent) == 2
        assert all(len(chain) == 1 for _, chain in context.sent)

    asyncio.run(scenario())
