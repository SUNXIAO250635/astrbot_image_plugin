from __future__ import annotations

import time
from dataclasses import dataclass

from .config import ProviderProfile, RoutingConfig
from .models import Capability, GenerationRequest, GenerationResult, ProviderFailure
from .provider import HandleCallback, MediaProvider


@dataclass(slots=True)
class _Health:
    failures: int = 0
    cooldown_until: float = 0.0


class ProviderRouter:
    def __init__(
        self,
        providers: list[MediaProvider],
        profiles: list[ProviderProfile],
        config: RoutingConfig,
    ):
        self.providers = {provider.provider_id: provider for provider in providers}
        self.profiles = {profile.provider_id: profile for profile in profiles}
        self.config = config
        self._health: dict[str, _Health] = {}
        self._cursor: dict[Capability, int] = {}

    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        candidates = self._candidates(request)
        if not candidates:
            raise ProviderFailure(
                f"没有供应商支持 {request.capability.value}",
                provider_id=request.provider_hint,
            )
        failures = []
        max_attempts = self.config.max_attempts or len(candidates)
        for provider in candidates[:max_attempts]:
            try:
                result = await provider.generate(request, on_handle)
                self._record_success(provider.provider_id)
                if failures:
                    result.attempts = [*failures, *result.attempts]
                return result
            except ProviderFailure as exc:
                failures.append(provider.provider_id)
                self._record_failure(provider.provider_id)
                if exc.accepted:
                    if not (
                        exc.retryable
                        and self.config.failover_after_terminal_failure
                    ):
                        raise
                elif not exc.retryable:
                    raise
                last_error = exc
        tried = ", ".join(failures)
        raise ProviderFailure(
            f"所有候选供应商均失败({tried}): {last_error}",
            kind=last_error.kind,
            status=last_error.status,
            retryable=False,
        ) from last_error

    def _candidates(self, request: GenerationRequest) -> list[MediaProvider]:
        providers = [
            provider
            for provider in self.providers.values()
            if provider.supports(request.capability)
        ]
        if request.provider_hint:
            providers = [
                provider
                for provider in providers
                if provider.provider_id == request.provider_hint
            ]
        providers.sort(key=lambda item: self._sort_key(item, request.capability))
        healthy = [provider for provider in providers if self._is_healthy(provider.provider_id)]
        if healthy:
            providers = healthy
        if self.config.mode == "round_robin" and len(providers) > 1:
            cursor = self._cursor.get(request.capability, 0) % len(providers)
            providers = providers[cursor:] + providers[:cursor]
            self._cursor[request.capability] = cursor + 1
        return providers

    def _sort_key(self, provider: MediaProvider, capability: Capability):
        profile = self.profiles[provider.provider_id]
        order = self.config.orders.get(capability) or []
        explicit_index = _index_or_none(order, provider.provider_id)
        type_index = _index_or_none(order, f"type:{profile.provider_type}")
        if explicit_index is not None:
            group = explicit_index
        elif type_index is not None:
            group = type_index
        else:
            group = len(order) + 1
        return (group, -profile.priority, profile.position, provider.provider_id)

    def _is_healthy(self, provider_id: str) -> bool:
        return self._health.get(provider_id, _Health()).cooldown_until <= time.monotonic()

    def _record_success(self, provider_id: str):
        self._health[provider_id] = _Health()

    def _record_failure(self, provider_id: str):
        health = self._health.setdefault(provider_id, _Health())
        health.failures += 1
        if health.failures >= self.config.failure_threshold:
            health.cooldown_until = time.monotonic() + self.config.cooldown_seconds


def _index_or_none(values: list[str], target: str):
    try:
        return values.index(target)
    except ValueError:
        return None
