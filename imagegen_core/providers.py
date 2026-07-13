from __future__ import annotations

import inspect
import time

try:
    from .. import adapters
    from ..media import extract_all_media
except ImportError:
    import adapters
    from media import extract_all_media

from .config import ProviderProfile
from .delivery import result_from_response
from .models import (
    Capability,
    ErrorKind,
    GenerationHandle,
    GenerationRequest,
    GenerationResult,
    MediaArtifact,
    ProviderFailure,
)
from .provider import HandleCallback


class OpenAICompatibleProvider:
    def __init__(self, profile: ProviderProfile):
        self.profile = profile
        self.provider_id = profile.provider_id
        self.provider_type = profile.provider_type
        self.capabilities = profile.capabilities

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        if not self.supports(request.capability):
            raise ProviderFailure(
                f"供应商 {self.provider_id} 不支持 {request.capability.value}",
                kind=ErrorKind.UNSUPPORTED,
                provider_id=self.provider_id,
            )
        started = time.monotonic()
        try:
            if request.capability == Capability.TEXT_TO_IMAGE:
                result = await self._generate_image(request, with_references=False)
            elif request.capability == Capability.IMAGE_TO_IMAGE:
                result = await self._generate_image(request, with_references=True)
            else:
                result = await self._generate_video(request, on_handle)
        except ProviderFailure:
            raise
        except adapters.ApiException as exc:
            raise self._provider_failure(exc) from exc
        result.elapsed_seconds = time.monotonic() - started
        return result

    async def resume(self, handle: GenerationHandle) -> GenerationResult:
        if handle.provider_id != self.provider_id:
            raise ProviderFailure(
                "视频任务供应商不匹配",
                kind=ErrorKind.INVALID_REQUEST,
                provider_id=self.provider_id,
                accepted=True,
                remote_task_id=handle.remote_task_id,
            )
        cfg = dict(self.profile.config)
        try:
            response = await adapters.poll_openai_video(
                cfg,
                handle.remote_task_id,
                {"task_id": handle.remote_task_id},
                self._timeout(cfg),
                self._proxy(cfg),
            )
        except adapters.ApiException as exc:
            failure = self._provider_failure(exc)
            failure.accepted = True
            failure.remote_task_id = handle.remote_task_id
            raise failure from exc
        result = self._video_result(response)
        result.remote_task_id = handle.remote_task_id
        return result

    async def _generate_image(
        self, request: GenerationRequest, *, with_references: bool
    ) -> GenerationResult:
        cfg = self._request_config(request)
        target_count = request.count
        if not request.count_explicit:
            try:
                target_count = max(1, min(10, int(cfg.get("n", request.count) or 1)))
            except (TypeError, ValueError):
                target_count = request.count
        image_api = str(cfg.get("image_api") or "generation").lower()
        images, filenames = self._reference_payload(request)
        if with_references and not images:
            raise ProviderFailure(
                "图生图请求缺少参考图片",
                kind=ErrorKind.INVALID_REQUEST,
                provider_id=self.provider_id,
            )

        async def invoke(count: int):
            call_cfg = {**cfg, "n": count}
            if with_references and image_api == "edits":
                return await adapters.image_edits(
                    call_cfg,
                    request.prompt,
                    images,
                    filenames,
                    self._timeout(cfg),
                    self._proxy(cfg),
                )
            return await adapters.image_generation(
                call_cfg,
                request.prompt,
                self._timeout(cfg),
                images if with_references else None,
                filenames if with_references else None,
                self._proxy(cfg),
            )

        response = await invoke(target_count)
        media = self._artifacts(response)
        attempts = [self.provider_id]
        while len(media) < target_count:
            try:
                extra = await invoke(1)
            except adapters.ApiException:
                break
            extra_media = self._artifacts(extra)
            before = len(media)
            for item in extra_media:
                if not any(existing.value == item.value for existing in media):
                    media.append(item)
                if len(media) >= target_count:
                    break
            attempts.append(self.provider_id)
            if len(media) == before:
                break
        if not media:
            raise ProviderFailure(
                "响应中未找到图片",
                kind=ErrorKind.PARSE,
                provider_id=self.provider_id,
                retryable=True,
            )
        warnings = []
        if len(media) < target_count:
            warnings.append(f"请求 {target_count} 张，实际获得 {len(media)} 张")
        return GenerationResult(
            provider_id=self.provider_id,
            media=media[:target_count],
            attempts=attempts,
            warnings=warnings,
            raw=response,
        )

    async def _generate_video(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None,
    ) -> GenerationResult:
        cfg = self._request_config(request)
        video_api = str(cfg.get("video_api") or "video").lower()
        images, filenames = self._reference_payload(request)
        if request.capability.needs_reference and not images:
            raise ProviderFailure(
                "图生视频请求缺少参考图片",
                kind=ErrorKind.INVALID_REQUEST,
                provider_id=self.provider_id,
            )
        if video_api == "chat":
            response = await adapters.openai_chat(
                cfg,
                request.prompt,
                image_bytes=images or None,
                image_filename=filenames or None,
                timeout=self._timeout(cfg),
                proxy=self._proxy(cfg),
            )
            return self._video_result(response)
        if video_api == "edits":
            response = await adapters.image_edits(
                cfg,
                request.prompt,
                images,
                filenames,
                self._timeout(cfg),
                self._proxy(cfg),
            )
            return self._video_result(response)

        response, task_id = await adapters.submit_openai_video(
            cfg,
            request.prompt,
            images[0] if images else None,
            filenames[0] if filenames else None,
            self._timeout(cfg),
            self._proxy(cfg),
        )
        if not task_id:
            return self._video_result(response)
        handle = GenerationHandle(
            provider_id=self.provider_id,
            remote_task_id=task_id,
            capability=request.capability,
            accepted_at=time.time(),
            poll_metadata={"config": self._safe_poll_config(cfg)},
        )
        if on_handle:
            callback_result = on_handle(handle)
            if inspect.isawaitable(callback_result):
                await callback_result
        try:
            response = await adapters.poll_openai_video(
                cfg,
                task_id,
                response,
                self._timeout(cfg),
                self._proxy(cfg),
            )
        except adapters.ApiException as exc:
            failure = self._provider_failure(exc)
            failure.accepted = True
            failure.remote_task_id = task_id
            failure.retryable = str(exc).startswith("视频生成失败:")
            raise failure from exc
        result = self._video_result(response)
        result.remote_task_id = task_id
        return result

    def _video_result(self, response: dict) -> GenerationResult:
        result = result_from_response(
            self.provider_id, response, extract_all_media
        )
        if not result.media:
            raise ProviderFailure(
                "响应中未找到视频或图片",
                kind=ErrorKind.PARSE,
                provider_id=self.provider_id,
                retryable=True,
            )
        return result

    def _artifacts(self, response: dict) -> list[MediaArtifact]:
        return [
            MediaArtifact(kind=kind, value=value, provider_id=self.provider_id)
            for kind, value in extract_all_media(response)
        ]

    def _request_config(self, request: GenerationRequest) -> dict:
        cfg = dict(self.profile.config)
        if request.size:
            cfg["size"] = request.size
        if request.duration is not None:
            cfg["seconds"] = request.duration
        cfg.update(request.extra.get("provider_options") or {})
        return cfg

    @staticmethod
    def _reference_payload(request: GenerationRequest) -> tuple[list[bytes], list[str]]:
        items = [reference for reference in request.references if reference.data]
        return (
            [reference.data for reference in items],
            [reference.filename or f"input_{index + 1}.png" for index, reference in enumerate(items)],
        )

    @staticmethod
    def _timeout(cfg: dict) -> int:
        try:
            return max(1, int(cfg.get("timeout", 300) or 300))
        except (TypeError, ValueError):
            return 300

    @staticmethod
    def _proxy(cfg: dict) -> str:
        return str(cfg.get("proxy") or "")

    @staticmethod
    def _safe_poll_config(cfg: dict) -> dict:
        return {
            key: cfg.get(key)
            for key in (
                "base_url",
                "model",
                "poll_interval",
                "poll_max_wait",
                "timeout",
                "proxy",
            )
            if cfg.get(key) not in (None, "")
        }

    def _provider_failure(self, exc: adapters.ApiException) -> ProviderFailure:
        status = exc.status
        if status in {400, 405, 422}:
            kind, retryable = ErrorKind.INVALID_REQUEST, False
        elif status in {401, 403}:
            kind, retryable = ErrorKind.AUTH, True
        elif status == 429:
            kind, retryable = ErrorKind.RATE_LIMIT, True
        elif status in {408, 504} or "超时" in str(exc) or "Timeout" in str(exc):
            kind, retryable = ErrorKind.TIMEOUT, True
        elif status and status >= 500:
            kind, retryable = ErrorKind.SERVER, True
        else:
            kind, retryable = ErrorKind.NETWORK, True
        return ProviderFailure(
            str(exc),
            kind=kind,
            provider_id=self.provider_id,
            status=status,
            retryable=retryable,
        )


def build_providers(profiles: list[ProviderProfile]):
    from .native_providers import build_provider

    return [build_provider(profile) for profile in profiles if profile.enabled]
