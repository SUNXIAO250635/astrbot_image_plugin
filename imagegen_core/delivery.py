from __future__ import annotations

from collections.abc import Callable

from .models import GenerationResult, MediaArtifact


def result_from_response(
    provider_id: str,
    response: dict,
    extract_media: Callable[[dict], list[tuple[str, str]]],
    *,
    remote_task_id: str = "",
) -> GenerationResult:
    media = [
        MediaArtifact(kind=kind, value=value, provider_id=provider_id)
        for kind, value in extract_media(response)
    ]
    return GenerationResult(
        provider_id=provider_id,
        media=media,
        remote_task_id=remote_task_id,
        raw=response,
    )
