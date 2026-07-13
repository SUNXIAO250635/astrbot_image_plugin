from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

import main
from tests.fakes.runtime import (
    FakeContext,
    FakeEvent,
    collect_async_generator,
    image_data_uri,
    plugin_config,
)


def test_text_to_image_command_uses_generation_adapter():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        event = FakeEvent()
        response = {"data": [{"url": "https://cdn.invalid/cat.png"}]}
        adapter = AsyncMock(return_value=response)

        with patch.object(main.adapters, "image_generation", adapter):
            results = await collect_async_generator(
                plugin.text_to_image(event, "画一只猫")
            )

        assert len(results) == 1
        assert results[0].kind == "image"
        assert results[0].value == "https://cdn.invalid/cat.png"
        assert adapter.await_args.args[1] == "画一只猫"

    asyncio.run(scenario())


def test_image_to_image_command_passes_current_image_to_edits_adapter():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        source = b"source-image"
        event = FakeEvent([Comp.Image(file=image_data_uri(source))])
        response = {"data": [{"url": "https://cdn.invalid/watercolor.png"}]}
        adapter = AsyncMock(return_value=response)

        with patch.object(main.adapters, "image_edits", adapter):
            results = await collect_async_generator(
                plugin.image_to_image(event, "改成水彩风格")
            )

        assert len(results) == 1
        assert results[0].kind == "image"
        assert results[0].value == "https://cdn.invalid/watercolor.png"
        assert adapter.await_args.args[2] == [source]
        assert adapter.await_args.args[3] == ["input.png"]

    asyncio.run(scenario())


def test_text_to_video_command_keeps_openai_video_path():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        event = FakeEvent()
        response = {"data": [{"url": "https://cdn.invalid/train.mp4"}]}
        adapter = AsyncMock(return_value=response)

        with patch.object(main.adapters, "openai_video", adapter):
            results = await collect_async_generator(
                plugin.text_to_video(event, "火车穿越雪山")
            )

        assert len(results) == 1
        assert results[0].kind == "chain"
        assert len(results[0].value) == 1
        assert isinstance(results[0].value[0], Comp.Video)
        assert results[0].value[0].url == "https://cdn.invalid/train.mp4"
        assert adapter.await_args.args[2:4] == (None, None)

    asyncio.run(scenario())


def test_image_to_video_command_passes_reference_to_video_adapter():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        source = b"video-reference"
        event = FakeEvent([Comp.Image(file=image_data_uri(source))])
        response = {"data": [{"url": "https://cdn.invalid/animated.mp4"}]}
        adapter = AsyncMock(return_value=response)

        with patch.object(main.adapters, "openai_video", adapter):
            results = await collect_async_generator(
                plugin.image_to_video(event, "让画面动起来")
            )

        assert len(results) == 1
        assert results[0].kind == "chain"
        assert isinstance(results[0].value[0], Comp.Video)
        assert adapter.await_args.args[2] == source
        assert adapter.await_args.args[3] == "input.png"

    asyncio.run(scenario())


def test_legacy_mode_applies_persistent_rate_limit():
    async def scenario():
        config = plugin_config()
        config["rate_limit"] = {"window_seconds": 3600, "user_limit": 1}
        plugin = main.ImageGenPlugin(FakeContext(), config)
        event = FakeEvent()
        adapter = AsyncMock(
            return_value={"data": [{"url": "https://cdn.invalid/cat.png"}]}
        )

        with patch.object(main.adapters, "image_generation", adapter):
            first = await plugin._do_text_to_image(event, "cat")
            second = await plugin._do_text_to_image(event, "cat again")

        assert first.kind == "image"
        assert second.kind == "plain"
        assert "限流" in second.value
        assert adapter.await_count == 1

    asyncio.run(scenario())
