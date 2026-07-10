from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from imagegen_core.config import ProviderProfile, RoutingConfig
from imagegen_core.models import (
    Capability,
    ErrorKind,
    GenerationRequest,
    GenerationResult,
    MediaArtifact,
    ProviderFailure,
)
from imagegen_core.router import ProviderRouter


class FakeProvider:
    def __init__(self, provider_id, capabilities, outcome, calls):
        self.provider_id = provider_id
        self.capabilities = frozenset(capabilities)
        self.outcome = outcome
        self.calls = calls

    def supports(self, capability):
        return capability in self.capabilities

    async def generate(self, request, on_handle=None):
        self.calls.append(self.provider_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _profile(provider_id, priority=0, position=0, provider_type="test"):
    return ProviderProfile(
        provider_id=provider_id,
        provider_type=provider_type,
        config={},
        capabilities=frozenset({Capability.TEXT_TO_IMAGE}),
        priority=priority,
        position=position,
    )


def _result(provider_id):
    return GenerationResult(
        provider_id=provider_id,
        media=[MediaArtifact("image", f"https://cdn.invalid/{provider_id}.png")],
    )


def test_router_fails_over_in_explicit_order():
    async def scenario():
        calls = []
        first = FakeProvider(
            "first",
            {Capability.TEXT_TO_IMAGE},
            ProviderFailure(
                "server error",
                kind=ErrorKind.SERVER,
                provider_id="first",
                retryable=True,
            ),
            calls,
        )
        second = FakeProvider(
            "second", {Capability.TEXT_TO_IMAGE}, _result("second"), calls
        )
        profiles = [_profile("first"), _profile("second", position=1)]
        router = ProviderRouter(
            [first, second],
            profiles,
            RoutingConfig(orders={Capability.TEXT_TO_IMAGE: ["first", "second"]}),
        )

        result = await router.generate(
            GenerationRequest(Capability.TEXT_TO_IMAGE, "cat")
        )

        assert calls == ["first", "second"]
        assert result.provider_id == "second"

    asyncio.run(scenario())


def test_router_does_not_fail_over_after_remote_task_acceptance():
    async def scenario():
        calls = []
        accepted = ProviderFailure(
            "poll timeout",
            kind=ErrorKind.TIMEOUT,
            provider_id="first",
            retryable=False,
            accepted=True,
            remote_task_id="task-1",
        )
        first = FakeProvider("first", {Capability.TEXT_TO_IMAGE}, accepted, calls)
        second = FakeProvider(
            "second", {Capability.TEXT_TO_IMAGE}, _result("second"), calls
        )
        profiles = [_profile("first"), _profile("second", position=1)]
        router = ProviderRouter(
            [first, second], profiles, RoutingConfig()
        )

        try:
            await router.generate(
                GenerationRequest(Capability.TEXT_TO_IMAGE, "cat")
            )
        except ProviderFailure as exc:
            assert exc.remote_task_id == "task-1"
        else:
            raise AssertionError("accepted failure should be returned")
        assert calls == ["first"]

    asyncio.run(scenario())


def test_router_uses_priority_inside_type_placeholder():
    async def scenario():
        calls = []
        low = FakeProvider("low", {Capability.TEXT_TO_IMAGE}, _result("low"), calls)
        high = FakeProvider("high", {Capability.TEXT_TO_IMAGE}, _result("high"), calls)
        profiles = [
            _profile("low", priority=1, provider_type="same"),
            _profile("high", priority=10, position=1, provider_type="same"),
        ]
        router = ProviderRouter(
            [low, high],
            profiles,
            RoutingConfig(orders={Capability.TEXT_TO_IMAGE: ["type:same"]}),
        )

        result = await router.generate(
            GenerationRequest(Capability.TEXT_TO_IMAGE, "cat")
        )

        assert calls == ["high"]
        assert result.provider_id == "high"

    asyncio.run(scenario())


def test_router_mode_uses_legacy_profiles_for_image_and_video(plugin_factory):
    async def scenario():
        plugin, event = plugin_factory(mode="router")
        image_call = AsyncMock(
            return_value={"data": [{"url": "https://cdn.invalid/cat.png"}]}
        )
        video_submit = AsyncMock(
            return_value=(
                {"data": [{"url": "https://cdn.invalid/cat.mp4"}]},
                None,
            )
        )

        with (
            patch("imagegen_core.providers.adapters.image_generation", image_call),
            patch(
                "imagegen_core.providers.adapters.submit_openai_video",
                video_submit,
            ),
        ):
            image_result = await plugin._do_text_to_image(event, "cat")
            video_result = await plugin._do_text_to_video(
                event, "animate", "openai_video"
            )

        assert image_result.kind == "image"
        assert image_result.value.endswith("cat.png")
        assert video_result.kind == "chain"
        assert video_result.value[0].url.endswith("cat.mp4")

    asyncio.run(scenario())
