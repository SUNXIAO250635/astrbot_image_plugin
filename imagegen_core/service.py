from __future__ import annotations

from .config import provider_profiles, routing_config
from .models import GenerationRequest, GenerationResult
from .provider import HandleCallback
from .providers import build_providers
from .router import ProviderRouter


class GenerationService:
    def __init__(self, config: dict):
        profiles = provider_profiles(config)
        self.router = ProviderRouter(
            build_providers(profiles), profiles, routing_config(config)
        )

    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        return await self.router.generate(request, on_handle)
