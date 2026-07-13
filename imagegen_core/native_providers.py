from __future__ import annotations

import base64
import asyncio
import inspect
import time
from urllib.parse import quote

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
    ProviderFailure,
)
from .provider import HandleCallback
from .providers import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        if request.capability not in {
            Capability.TEXT_TO_IMAGE,
            Capability.IMAGE_TO_IMAGE,
        }:
            return await super().generate(request, on_handle)
        cfg = self._request_config(request)
        model = str(cfg.get("model") or "gemini-2.5-flash-image")
        base_url = str(
            cfg.get("base_url")
            or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        if ":generateContent" in base_url:
            url = base_url
        else:
            url = f"{base_url}/models/{quote(model, safe='')}:generateContent"
        api_key = str(cfg.get("api_key") or "")
        if api_key and "key=" not in url:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}key={quote(api_key, safe='')}"

        parts = [{"text": request.prompt}]
        for reference in request.references:
            if reference.data:
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": reference.mime_type or "image/png",
                            "data": base64.b64encode(reference.data).decode(),
                        }
                    }
                )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "candidateCount": request.count,
            },
        }
        try:
            response = await adapters._post_json(
                url, {}, payload, self._timeout(cfg), self._proxy(cfg)
            )
        except adapters.ApiException as exc:
            raise self._provider_failure(exc) from exc
        normalized = _normalize_gemini_response(response)
        result = result_from_response(
            self.provider_id, normalized, extract_all_media
        )
        if not result.media:
            raise ProviderFailure(
                "Gemini 响应中未找到图片",
                kind=ErrorKind.PARSE,
                provider_id=self.provider_id,
                retryable=True,
            )
        result.raw = response
        return result


class GenericJsonProvider(OpenAICompatibleProvider):
    async def generate(
        self,
        request: GenerationRequest,
        on_handle: HandleCallback | None = None,
    ) -> GenerationResult:
        cfg = self._request_config(request)
        path = self._path_for(request.capability, cfg)
        url = adapters._join(cfg.get("base_url", ""), path)
        payload = self._payload(request, cfg)
        try:
            response = await adapters._post_json(
                url,
                adapters._auth_headers(cfg.get("api_key", "")),
                payload,
                self._timeout(cfg),
                self._proxy(cfg),
            )
        except adapters.ApiException as exc:
            raise self._provider_failure(exc) from exc
        if extract_all_media(response):
            return result_from_response(
                self.provider_id, response, extract_all_media
            )
        task_id = adapters._video_task_id(response)
        if not task_id:
            raise ProviderFailure(
                "通用 JSON 响应中既没有媒体也没有 task id",
                kind=ErrorKind.PARSE,
                provider_id=self.provider_id,
                retryable=True,
            )
        handle = GenerationHandle(
            provider_id=self.provider_id,
            remote_task_id=task_id,
            capability=request.capability,
            accepted_at=time.time(),
            poll_metadata={"poll_path": str(cfg.get("poll_path") or "")},
        )
        if on_handle:
            callback_result = on_handle(handle)
            if inspect.isawaitable(callback_result):
                await callback_result
        return await self._poll_generic(cfg, handle, response)

    async def resume(self, handle: GenerationHandle) -> GenerationResult:
        return await self._poll_generic(
            dict(self.profile.config), handle, {"task_id": handle.remote_task_id}
        )

    async def _poll_generic(
        self, cfg: dict, handle: GenerationHandle, initial: dict
    ) -> GenerationResult:
        default_poll_path = (
            "/v1/query/video_generation?task_id={task_id}"
            if self.provider_type == "minimax"
            else "/v1/video/generations/{task_id}"
        )
        poll_path = str(
            handle.poll_metadata.get("poll_path")
            or cfg.get("poll_path")
            or default_poll_path
        ).replace("{task_id}", quote(handle.remote_task_id, safe=""))
        poll_url = adapters._join(cfg.get("base_url", ""), poll_path)
        interval = adapters._safe_float(
            cfg.get("poll_interval"), 3, minimum=0.5, maximum=60
        )
        max_wait = adapters._safe_int(
            cfg.get("poll_max_wait"), 600, minimum=1, maximum=86400
        )
        deadline = time.monotonic() + max_wait
        last = initial
        while time.monotonic() < deadline:
            try:
                last = await adapters._get_json(
                    poll_url,
                    adapters._auth_headers(cfg.get("api_key", "")),
                    self._timeout(cfg),
                    self._proxy(cfg),
                )
            except adapters.ApiException as exc:
                if exc.status in {400, 401, 403, 404, 405, 422}:
                    failure = self._provider_failure(exc)
                    failure.accepted = True
                    failure.remote_task_id = handle.remote_task_id
                    raise failure from exc
                await asyncio.sleep(interval)
                continue
            if extract_all_media(last):
                result = result_from_response(
                    self.provider_id, last, extract_all_media,
                    remote_task_id=handle.remote_task_id,
                )
                return result
            status = adapters._video_status(last).lower()
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise ProviderFailure(
                    f"远端任务失败: {adapters._video_fail_reason(last) or status}",
                    kind=ErrorKind.SERVER,
                    provider_id=self.provider_id,
                    retryable=True,
                    accepted=True,
                    remote_task_id=handle.remote_task_id,
                )
            if status in {"success", "succeeded", "completed"}:
                completed = await self._fetch_completed_result(cfg, last)
                if completed:
                    return result_from_response(
                        self.provider_id,
                        completed,
                        extract_all_media,
                        remote_task_id=handle.remote_task_id,
                    )
            await asyncio.sleep(interval)
        raise ProviderFailure(
            f"远端任务轮询超时(> {max_wait}s)",
            kind=ErrorKind.TIMEOUT,
            provider_id=self.provider_id,
            retryable=False,
            accepted=True,
            remote_task_id=handle.remote_task_id,
        )

    def _path_for(self, capability: Capability, cfg: dict) -> str:
        if self.provider_type == "minimax":
            default_path = (
                "/v1/image_generation"
                if capability.media_kind == "image"
                else "/v1/video_generation"
            )
        else:
            default_path = (
                "/v1/images/generations"
                if capability.media_kind == "image"
                else "/v1/video/generations"
            )
        return str(
            cfg.get(f"{capability.value}_path")
            or (
                cfg.get("image_path")
                if capability.media_kind == "image"
                else cfg.get("video_path")
            )
            or default_path
        )

    async def _fetch_completed_result(self, cfg: dict, response: dict) -> dict | None:
        file_id = _first_nested_value(response, ("file_id", "fileId"))
        result_path = str(cfg.get("result_path") or "")
        if not result_path and self.provider_type == "minimax" and file_id:
            result_path = "/v1/files/retrieve?file_id={file_id}"
        if not result_path or not file_id:
            return None
        url = adapters._join(
            cfg.get("base_url", ""),
            result_path.replace("{file_id}", quote(str(file_id), safe="")),
        )
        try:
            result = await adapters._get_json(
                url,
                adapters._auth_headers(cfg.get("api_key", "")),
                self._timeout(cfg),
                self._proxy(cfg),
            )
        except adapters.ApiException:
            return None
        return result if extract_all_media(result) else None

    @staticmethod
    def _payload(request: GenerationRequest, cfg: dict) -> dict:
        count = request.count
        if not request.count_explicit:
            try:
                count = max(1, min(10, int(cfg.get("n", request.count) or 1)))
            except (TypeError, ValueError):
                count = request.count
        payload = {
            "model": cfg.get("model", ""),
            "prompt": request.prompt,
            "n": count,
        }
        if request.size or cfg.get("size"):
            payload["size"] = request.size or cfg.get("size")
        if request.duration is not None or cfg.get("seconds"):
            payload["seconds"] = request.duration or cfg.get("seconds")
        images = [item.data for item in request.references if item.data]
        names = [item.filename for item in request.references if item.data]
        if images:
            payload["image"] = adapters._image_payload(images, names)
        extra = cfg.get("extra_json")
        if isinstance(extra, dict):
            payload.update(extra)
        return payload


def build_provider(profile: ProviderProfile):
    protocol = str(profile.config.get("protocol") or "").strip().lower()
    if profile.provider_type == "google_gemini" or protocol == "gemini":
        return GeminiProvider(profile)
    if protocol == "generic_json" or profile.provider_type in {"minimax", "sensenova"}:
        return GenericJsonProvider(profile)
    return OpenAICompatibleProvider(profile)


def _normalize_gemini_response(response: dict) -> dict:
    data = []
    candidates = response.get("candidates") if isinstance(response, dict) else None
    for candidate in candidates or []:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                data.append(f"data:{mime};base64,{inline['data']}")
            file_data = part.get("fileData") or part.get("file_data")
            if isinstance(file_data, dict):
                uri = file_data.get("fileUri") or file_data.get("file_uri")
                if uri:
                    data.append({"url": uri})
    return {"data": data}


def _first_nested_value(value, keys: tuple[str, ...]):
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) not in (None, ""):
                return value.get(key)
        for nested in value.values():
            found = _first_nested_value(nested, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _first_nested_value(nested, keys)
            if found not in (None, ""):
                return found
    return None
