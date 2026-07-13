from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from imagegen_core.config import ProviderProfile, parse_capabilities, provider_profiles
from imagegen_core.models import (
    Capability,
    GenerationRequest,
    ReferenceAsset,
)
from imagegen_core.native_providers import (
    GeminiProvider,
    GenericJsonProvider,
    build_provider,
)
from imagegen_core.providers import OpenAICompatibleProvider


def _profile(provider_type, capabilities, **config):
    return ProviderProfile(
        provider_id=f"{provider_type}-1",
        provider_type=provider_type,
        config={"base_url": "https://api.invalid", "model": "model", **config},
        capabilities=frozenset(capabilities),
    )


def test_provider_registry_covers_all_configured_supplier_types():
    openai_types = {
        "openai_compat",
        "openai_images",
        "agnes",
        "xai",
        "stepfun",
        "zai",
        "grok2api",
        "doubao",
    }
    for provider_type in openai_types:
        provider = build_provider(_profile(provider_type, {Capability.TEXT_TO_IMAGE}))
        assert isinstance(provider, OpenAICompatibleProvider)
    assert isinstance(
        build_provider(_profile("google_gemini", {Capability.TEXT_TO_IMAGE})),
        GeminiProvider,
    )
    assert isinstance(
        build_provider(_profile("minimax", {Capability.TEXT_TO_VIDEO})),
        GenericJsonProvider,
    )
    assert isinstance(
        build_provider(_profile("sensenova", {Capability.TEXT_TO_VIDEO})),
        GenericJsonProvider,
    )
    assert parse_capabilities("", "google_gemini") == frozenset(
        {Capability.TEXT_TO_IMAGE, Capability.IMAGE_TO_IMAGE}
    )


def test_duplicate_provider_ids_are_rejected():
    with pytest.raises(ValueError, match="重复 provider_id"):
        provider_profiles(
            {
                "providers": [
                    {"provider_id": "same", "provider_type": "openai_compat"},
                    {"provider_id": "same", "provider_type": "openai_images"},
                ]
            }
        )


def test_unknown_provider_capability_and_protocol_are_rejected():
    with pytest.raises(ValueError, match="未知 capabilities"):
        provider_profiles(
            {
                "providers": [
                    {
                        "provider_id": "bad-capability",
                        "capabilities": "text_to_imgae",
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="未知 provider protocol"):
        provider_profiles(
            {
                "providers": [
                    {
                        "provider_id": "bad-protocol",
                        "protocol": "openai_compatiable",
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("configured_count", "request_count", "count_explicit", "expected_count"),
    [
        (3, 1, False, 3),
        (4, 2, True, 2),
    ],
)
def test_openai_provider_respects_configured_and_explicit_image_counts(
    configured_count, request_count, count_explicit, expected_count
):
    async def scenario():
        image_call = AsyncMock(
            return_value={
                "data": [
                    {"url": f"https://cdn.invalid/{index}.png"}
                    for index in range(expected_count)
                ]
            }
        )
        provider = OpenAICompatibleProvider(
            _profile(
                "openai_images",
                {Capability.TEXT_TO_IMAGE},
                n=configured_count,
            )
        )
        request = GenerationRequest(
            Capability.TEXT_TO_IMAGE,
            "cat",
            count=request_count,
            count_explicit=count_explicit,
        )

        with patch("imagegen_core.providers.adapters.image_generation", image_call):
            result = await provider.generate(request)

        assert image_call.await_args.args[0]["n"] == expected_count
        assert len(result.media) == expected_count

    asyncio.run(scenario())


def test_gemini_native_codec_sends_inline_reference_and_parses_image():
    async def scenario():
        encoded = base64.b64encode(b"generated").decode()
        post = AsyncMock(
            return_value={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": encoded,
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        )
        profile = _profile(
            "google_gemini",
            {Capability.IMAGE_TO_IMAGE},
            api_key="test-key",
        )
        provider = GeminiProvider(profile)
        request = GenerationRequest(
            Capability.IMAGE_TO_IMAGE,
            "change style",
            references=[
                ReferenceAsset(
                    1,
                    "current",
                    filename="source.png",
                    data=b"source",
                )
            ],
        )

        with patch("imagegen_core.native_providers.adapters._post_json", post):
            result = await provider.generate(request)

        url, _headers, payload = post.await_args.args[:3]
        assert "generateContent?key=test-key" in url
        assert payload["contents"][0]["parts"][1]["inlineData"]["data"]
        assert result.media[0].value == f"data:image/png;base64,{encoded}"

    asyncio.run(scenario())


def test_minimax_generic_video_codec_submits_polls_and_retrieves_file():
    async def scenario():
        post = AsyncMock(return_value={"task_id": "task-1"})
        get = AsyncMock(
            side_effect=[
                {"status": "Success", "file_id": "file-1"},
                {"file": {"download_url": "https://cdn.invalid/video.mp4"}},
            ]
        )
        sleep = AsyncMock()
        handles = []
        provider = GenericJsonProvider(_profile("minimax", {Capability.TEXT_TO_VIDEO}))

        with (
            patch("imagegen_core.native_providers.adapters._post_json", post),
            patch("imagegen_core.native_providers.adapters._get_json", get),
            patch("imagegen_core.native_providers.asyncio.sleep", sleep),
        ):
            result = await provider.generate(
                GenerationRequest(Capability.TEXT_TO_VIDEO, "animate"),
                lambda handle: handles.append(handle),
            )

        assert post.await_args.args[0].endswith("/v1/video_generation")
        assert "query/video_generation?task_id=task-1" in get.await_args_list[0].args[0]
        assert "files/retrieve?file_id=file-1" in get.await_args_list[1].args[0]
        assert handles[0].remote_task_id == "task-1"
        assert result.media[0].value.endswith("video.mp4")

    asyncio.run(scenario())
