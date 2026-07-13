from __future__ import annotations

from .config import provider_profiles, routing_config
from .models import GenerationHandle, GenerationRequest, GenerationResult, ProviderFailure
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

    async def resume(self, handle: GenerationHandle) -> GenerationResult:
        provider = self.router.providers.get(handle.provider_id)
        resume = getattr(provider, "resume", None) if provider else None
        if not callable(resume):
            raise ProviderFailure(
                f"无法恢复供应商 {handle.provider_id} 的远端任务",
                provider_id=handle.provider_id,
                accepted=True,
                remote_task_id=handle.remote_task_id,
            )
        return await resume(handle)
