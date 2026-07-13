from __future__ import annotations

import asyncio
from pathlib import Path

import astrbot.api.message_components as Comp

import main
from media import extract_all_media
from tests.fakes.runtime import FakeContext, FakeEvent, plugin_config


MULTI_IMAGE_RESPONSE = {
    "data": [
        {"url": "https://cdn.invalid/one.png"},
        {"url": "https://cdn.invalid/two.png"},
        {"url": "https://cdn.invalid/three.png"},
    ]
}


def test_multiple_images_are_sent_sequentially_and_final_result_stays_single():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        event = FakeEvent()

        result = await plugin._send_result(event, MULTI_IMAGE_RESPONSE, "文生图")

        assert len(event.sent) == 2
        assert all(len(chain) == 1 for chain in event.sent)
        assert all(isinstance(chain[0], Comp.Image) for chain in event.sent)
        assert result.kind == "chain"
        assert len(result.value) == 1
        assert result.value[0].url == "https://cdn.invalid/three.png"

    asyncio.run(scenario())


def test_sequential_send_failure_does_not_fallback_to_multi_image_chain():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        event = FakeEvent(fail_send_calls={1})

        result = await plugin._send_result(event, MULTI_IMAGE_RESPONSE, "文生图")

        assert event._send_calls == 2
        assert len(event.sent) == 1
        assert len(event.sent[0]) == 1
        assert result.kind == "chain"
        assert len(result.value) == 2
        assert isinstance(result.value[0], Comp.Plain)
        assert isinstance(result.value[1], Comp.Image)
        assert result.value[1].url == "https://cdn.invalid/three.png"

    asyncio.run(scenario())


def test_partial_media_is_preserved_when_backfill_request_fails():
    async def scenario():
        plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
        response = {"data": [{"url": "https://cdn.invalid/one.png"}]}

        async def fail_request(_count):
            raise RuntimeError("upstream unavailable")

        completed = await plugin._complete_requested_media_count(
            response, 3, fail_request, "文生图"
        )

        assert extract_all_media(completed) == [
            ("image", "https://cdn.invalid/one.png")
        ]

    asyncio.run(scenario())


def test_local_media_must_stay_inside_plugin_managed_directory(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.chdir(tmp_path)
        config = plugin_config()
        config["media"]["save_dir"] = "managed"
        plugin = main.ImageGenPlugin(FakeContext(), config)
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"outside")
        managed = Path(plugin._save_dir)
        managed.mkdir(parents=True, exist_ok=True)
        inside = managed / "inside.png"
        inside.write_bytes(b"inside")

        rejected = await plugin._send_result(
            FakeEvent(), {"data": [{"url": str(outside)}]}, "image"
        )
        accepted = await plugin._send_result(
            FakeEvent(), {"data": [{"url": str(inside)}]}, "image"
        )

        assert rejected.kind == "plain"
        assert "受管目录外" in rejected.value
        assert accepted.kind == "image"
        assert Path(accepted.value) == inside

    asyncio.run(scenario())


def test_save_dir_cannot_escape_data_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = plugin_config()
    config["media"]["save_dir"] = "../outside"

    plugin = main.ImageGenPlugin(FakeContext(), config)

    assert Path(plugin._save_dir) == (tmp_path / "data" / "imagegen").resolve()
