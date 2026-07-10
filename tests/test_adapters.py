from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import adapters


def test_seedream_false_watermark_is_sent_but_non_seedream_false_is_omitted():
    async def scenario():
        post = AsyncMock(return_value={"data": []})
        base = {
            "base_url": "https://images.invalid",
            "watermark": "false",
            "size": "1024x1024",
            "n": 1,
        }
        with patch.object(adapters, "_post_json", post):
            await adapters.image_generation(
                {**base, "model": "doubao-seedream-4.5"}, "cat", 30
            )
            seedream_payload = post.await_args.args[2]
            await adapters.image_generation(
                {**base, "model": "gpt-image-1"}, "cat", 30
            )
            other_payload = post.await_args.args[2]

        assert seedream_payload["watermark"] is False
        assert "watermark" not in other_payload

    asyncio.run(scenario())


def test_video_adapter_polls_nested_task_id_until_success():
    async def scenario():
        post = AsyncMock(return_value={"data": {"id": "task-123"}})
        get = AsyncMock(
            side_effect=[
                {"data": {"status": "RUNNING"}},
                {
                    "data": {
                        "status": "SUCCESS",
                        "result_url": "https://cdn.invalid/video.mp4",
                    }
                },
            ]
        )
        sleep = AsyncMock()
        config = {
            "base_url": "https://video.invalid",
            "model": "test-video",
            "seconds": 999,
            "poll_interval": 0,
            "poll_max_wait": 30,
        }

        with (
            patch.object(adapters, "_post_json", post),
            patch.object(adapters, "_get_json", get),
            patch.object(adapters.asyncio, "sleep", sleep),
        ):
            result = await adapters.openai_video(config, "animate", timeout=30)

        assert post.await_args.args[2]["seconds"] == 60
        assert get.await_args_list[0].args[0].endswith(
            "/v1/video/generations/task-123"
        )
        assert result["data"]["status"] == "SUCCESS"
        assert sleep.await_count == 1

    asyncio.run(scenario())


def test_video_adapter_fails_fast_on_permanent_poll_error():
    async def scenario():
        post = AsyncMock(return_value={"task_id": "task-401"})
        get = AsyncMock(side_effect=adapters.ApiException("unauthorized", status=401))
        sleep = AsyncMock()
        config = {
            "base_url": "https://video.invalid",
            "poll_max_wait": 30,
        }

        with (
            patch.object(adapters, "_post_json", post),
            patch.object(adapters, "_get_json", get),
            patch.object(adapters.asyncio, "sleep", sleep),
            pytest.raises(adapters.ApiException, match="unauthorized"),
        ):
            await adapters.openai_video(config, "animate", timeout=30)

        sleep.assert_not_awaited()

    asyncio.run(scenario())


def test_json_transport_timeout_is_wrapped_as_api_exception():
    class TimeoutSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise asyncio.TimeoutError("simulated timeout")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def scenario():
        with (
            patch.object(adapters.aiohttp, "ClientSession", TimeoutSession),
            pytest.raises(adapters.ApiException, match="POST 请求失败"),
        ):
            await adapters._post_json(
                "https://api.invalid/v1/test", {}, {"test": True}, 1
            )

    asyncio.run(scenario())
