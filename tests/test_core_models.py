from __future__ import annotations

from imagegen_core.delivery import result_from_response
from imagegen_core.models import (
    CallerContext,
    Capability,
    GenerationHandle,
    GenerationRequest,
    MediaArtifact,
)
from media import extract_all_media


def test_capability_exposes_media_kind_and_reference_requirement():
    assert Capability.TEXT_TO_IMAGE.media_kind == "image"
    assert Capability.TEXT_TO_VIDEO.media_kind == "video"
    assert Capability.IMAGE_TO_IMAGE.needs_reference is True
    assert Capability.TEXT_TO_VIDEO.needs_reference is False


def test_generation_request_clamps_count_and_normalizes_prompt():
    request = GenerationRequest(
        capability=Capability.TEXT_TO_IMAGE,
        prompt="  cat  ",
        count=99,
    )

    assert request.prompt == "cat"
    assert request.count == 10


def test_generation_handle_round_trips_for_persistence():
    handle = GenerationHandle(
        provider_id="video-primary",
        remote_task_id="task-1",
        capability=Capability.IMAGE_TO_VIDEO,
        accepted_at=123.5,
        poll_metadata={"path": "/v1/video/generations/task-1"},
    )

    restored = GenerationHandle.from_dict(handle.to_dict())

    assert restored == handle


def test_caller_context_round_trips_for_background_delivery():
    caller = CallerContext(
        unified_msg_origin="test:group:1",
        sender_id="user-1",
        group_id="group-1",
        platform="test",
    )

    assert CallerContext.from_dict(caller.to_dict()) == caller


def test_delivery_normalizes_raw_response_to_generation_result():
    response = {"data": [{"url": "https://cdn.invalid/image.png"}]}

    result = result_from_response("primary", response, extract_all_media)

    assert result.media == [
        MediaArtifact(
            kind="image",
            value="https://cdn.invalid/image.png",
            provider_id="primary",
        )
    ]
    assert result.as_response() == response
