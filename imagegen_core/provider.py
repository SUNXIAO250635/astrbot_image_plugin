from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from .models import (
    Capability,
    GenerationHandle,
    GenerationRequest,
    GenerationResult,
)

HandleCallback = Callable[[GenerationHandle], Awaitable[None] | None]


class MediaProvider(Protocol):
    provider_id: str
    capabilities: frozenset[Capability]

    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        ...

    def supports(self, capability: Capability) -> bool:
        ...
