"""四个接口适配器的请求封装。

每个适配器返回 dict(响应 JSON)；媒体提取统一交给 media.extract_media。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Optional

import aiohttp


async def get_session():
    """Import lazily so standalone adapter imports do not initialize core eagerly."""
    try:
        from .imagegen_core.http_client import get_session as shared_get_session
    except ImportError:
        from imagegen_core.http_client import get_session as shared_get_session
    return await shared_get_session()


class ApiException(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _with_default_headers(headers: dict, **extra) -> dict:
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        **(headers or {}),
        **extra,
    }


def _join(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ApiException("请先在插件配置中填写 base_url")
    path = "/" + (path or "").lstrip("/")
    # base_url 可能本身就是完整 endpoint，避免重复拼接。
    if base.endswith(path):
        return base
    # base_url 常见填法是 https://api.example.com/v1；此时 path 若也以 /v1/
    # 开头，需要避免拼成 /v1/v1/...
    if path.startswith("/v1/") and base.endswith("/v1"):
        return f"{base}{path[3:]}"
    return f"{base}{path}"


async def _get_json(url: str, headers: dict, timeout: int, proxy: str = "") -> dict:
    import json as _json

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = _with_default_headers(headers, Accept="application/json")
    proxy_kw = {"proxy": proxy} if proxy else {}
    try:
        session = await get_session()
        async with session.get(
            url, headers=headers, timeout=timeout_cfg, **proxy_kw
        ) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(
                    f"接口返回 {resp.status}: {text[:300]}", status=resp.status
                )
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")
    except ApiException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ApiException(f"GET 请求失败: {type(e).__name__}: {e}") from e


async def _post_json(
    url: str, headers: dict, payload: dict, timeout: int, proxy: str = ""
) -> dict:
    import json as _json

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = _with_default_headers(
        headers, **{"Content-Type": "application/json", "Accept": "application/json"}
    )
    proxy_kw = {"proxy": proxy} if proxy else {}
    try:
        session = await get_session()
        async with session.post(
            url, headers=headers, json=payload, timeout=timeout_cfg, **proxy_kw
        ) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(
                    f"接口返回 {resp.status}: {text[:300]}", status=resp.status
                )
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")
    except ApiException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ApiException(f"POST 请求失败: {type(e).__name__}: {e}") from e


async def _post_multipart(
    url: str, headers: dict, form_fields: list, timeout: int, proxy: str = ""
) -> dict:
    """form_fields: list of (name, value, filename?, content_type?)。"""
    import json as _json
    from aiohttp import FormData

    form = FormData()
    for f in form_fields:
        if len(f) == 2:
            form.add_field(f[0], f[1])
        elif len(f) == 3:
            form.add_field(f[0], f[1], filename=f[2])
        else:
            form.add_field(f[0], f[1], filename=f[2], content_type=f[3])

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = _with_default_headers(headers, Accept="application/json")
    proxy_kw = {"proxy": proxy} if proxy else {}
    try:
        session = await get_session()
        async with session.post(
            url, headers=headers, data=form, timeout=timeout_cfg, **proxy_kw
        ) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(
                    f"接口返回 {resp.status}: {text[:300]}", status=resp.status
                )
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")
    except ApiException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ApiException(f"上传请求失败: {type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------- #
# 1) image-generation  /v1/images/generations
# --------------------------------------------------------------------------- #
async def image_generation(
    cfg: dict,
    prompt: str,
    timeout: int,
    image_bytes=None,
    image_filename=None,
    proxy: str = "",
) -> dict:
    model = cfg.get("model", "dall-e-3")
    url = _join(cfg.get("base_url", ""), "/v1/images/generations")
    payload = {
        "model": model,
        "prompt": prompt,
        "n": int(cfg.get("n", 1) or 1),
        "size": cfg.get("size", "1024x1024"),
    }
    watermark = _watermark_value(cfg, model)
    if watermark is not None:
        payload["watermark"] = watermark
    if image_bytes is not None:
        payload["image"] = _image_payload(image_bytes, image_filename)
    return await _post_json(
        url, _auth_headers(cfg.get("api_key", "")), payload, timeout, proxy
    )


def _watermark_value(cfg: dict, model: str) -> Optional[bool]:
    raw = cfg.get("watermark", "false")
    normalized = str(raw).strip().lower()
    if normalized in {"", "auto", "default", "none"}:
        return None
    if normalized in {"0", "false", "no", "off", "关闭", "禁用"}:
        model_lower = (model or "").lower()
        if "seedream" not in model_lower:
            return None
    return _as_bool(raw)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "开启", "启用"}


def _image_payload(image_bytes, image_filename=None):
    images = _normalize_images(image_bytes, image_filename)
    data_uris = [_image_data_uri(data, name) for data, name in images]
    if not data_uris:
        return None
    return data_uris[0] if len(data_uris) == 1 else data_uris


def _normalize_images(image_bytes, image_filename=None) -> list:
    if image_bytes is None:
        return []
    if isinstance(image_bytes, (list, tuple)):
        filenames = image_filename if isinstance(image_filename, (list, tuple)) else []
        return [
            (data, filenames[i] if i < len(filenames) else f"input_{i + 1}.png")
            for i, data in enumerate(image_bytes)
            if data is not None
        ]
    return [(image_bytes, image_filename or "input.png")]


def _image_content_type(image_filename: Optional[str] = None) -> str:
    filename = (image_filename or "").lower()
    if filename.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if filename.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _image_data_uri(image_bytes: bytes, image_filename: Optional[str] = None) -> str:
    mime = _image_content_type(image_filename)
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


# --------------------------------------------------------------------------- #
# 2) image-edits  /v1/images/edits   (multipart)
# --------------------------------------------------------------------------- #
async def image_edits(
    cfg: dict,
    prompt: str,
    image_bytes,
    image_filename,
    timeout: int,
    proxy: str = "",
) -> dict:
    base_url = cfg.get("base_url", "")
    url = _join(base_url, "/images/edits")
    # 部分 base_url 形如 https://api.x/v1 ，我们也补 /v1
    if "/v1" not in url:
        url = _join(base_url, "/v1/images/edits")

    fields = [
        ("model", cfg.get("model", "gpt-image-1")),
        ("prompt", prompt),
        ("n", str(cfg.get("n", 1) or 1)),
        ("size", cfg.get("size", "1024x1024")),
    ]
    for data, filename in _normalize_images(image_bytes, image_filename):
        fields.append(("image", data, filename, _image_content_type(filename)))
    return await _post_multipart(
        url, _auth_headers(cfg.get("api_key", "")), fields, timeout, proxy
    )


# --------------------------------------------------------------------------- #
# 3) openai  /v1/chat/completions
# --------------------------------------------------------------------------- #
async def openai_chat(
    cfg: dict,
    prompt: str,
    image_bytes=None,
    image_filename=None,
    timeout: int = 180,
    proxy: str = "",
) -> dict:
    url = _join(cfg.get("base_url", ""), "/v1/chat/completions")
    messages = []
    sysp = cfg.get("system_prompt", "")
    if sysp:
        messages.append({"role": "system", "content": sysp})

    images = _normalize_images(image_bytes, image_filename)
    if not images:
        messages.append({"role": "user", "content": prompt})
    else:
        # 多模态：把图片作为 image_url 内联 base64
        content = [{"type": "text", "text": prompt}]
        for index, (data, filename) in enumerate(images, start=1):
            content.append({"type": "text", "text": f"图 {index} / image {index}"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_uri(data, filename)},
                }
            )
        messages.append(
            {
                "role": "user",
                "content": content,
            }
        )

    payload = {
        "model": cfg.get("model", "gpt-4o"),
        "messages": messages,
    }
    return await _post_json(
        url, _auth_headers(cfg.get("api_key", "")), payload, timeout, proxy
    )


# --------------------------------------------------------------------------- #
# 4) openai-video  /v1/video/generations
# --------------------------------------------------------------------------- #
async def submit_openai_video(
    cfg: dict,
    prompt: str,
    image_bytes: Optional[bytes] = None,
    image_filename: Optional[str] = None,
    timeout: int = 300,
    proxy: str = "",
) -> tuple[dict, Optional[str]]:
    """提交文生视频/图生视频任务，返回提交响应和可选 task id。"""
    import base64 as _b64
    from aiohttp import FormData

    submit_url = _join(cfg.get("base_url", ""), "/v1/video/generations")
    headers = _auth_headers(cfg.get("api_key", ""))
    seconds = _safe_int(cfg.get("seconds"), 8, minimum=1, maximum=60)

    if image_bytes is None:
        # 文生视频：JSON
        payload = {
            "model": cfg.get("model", "sora"),
            "prompt": prompt,
            "seconds": seconds,
        }
        submit_resp = await _post_json(submit_url, headers, payload, timeout, proxy)
    else:
        # 图生视频：multipart，带 image
        form = FormData()
        form.add_field("model", cfg.get("model", "sora"))
        form.add_field("prompt", prompt)
        form.add_field("seconds", str(seconds))
        # 优先传 base64 image，兼容更多后端
        try:
            b64 = _b64.b64encode(image_bytes).decode()
            content_type = _image_content_type(image_filename)
            form.add_field("image", f"data:{content_type};base64,{b64}")
            form.add_field(
                "image_file",
                image_bytes,
                filename=image_filename or "input.png",
                content_type=content_type,
            )
        except Exception:
            form.add_field(
                "image",
                image_bytes,
                filename=image_filename or "input.png",
                content_type=_image_content_type(image_filename),
            )

        import json as _json

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        request_headers = _with_default_headers(headers, Accept="application/json")
        proxy_kw = {"proxy": proxy} if proxy else {}
        try:
            session = await get_session()
            async with session.post(
                submit_url,
                headers=request_headers,
                data=form,
                timeout=timeout_cfg,
                **proxy_kw,
            ) as resp:
                text = await resp.text(errors="ignore")
                if resp.status >= 400:
                    raise ApiException(
                        f"接口返回 {resp.status}: {text[:300]}",
                        status=resp.status,
                    )
                try:
                    submit_resp = _json.loads(text)
                except Exception:
                    raise ApiException(f"响应非 JSON: {text[:300]}")
        except ApiException:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ApiException(f"视频上传请求失败: {type(e).__name__}: {e}") from e

    return submit_resp, _video_task_id(submit_resp)


async def poll_openai_video(
    cfg: dict,
    task_id: str,
    initial_response: Optional[dict] = None,
    timeout: int = 300,
    proxy: str = "",
) -> dict:
    """轮询已提交的视频任务；该函数不会创建新的远端任务。"""
    submit_url = _join(cfg.get("base_url", ""), "/v1/video/generations")
    headers = _auth_headers(cfg.get("api_key", ""))

    # 轮询：data.status 达到 SUCCESS/COMPLETED/video.success 即结束
    poll_url = f"{submit_url}/{task_id}"
    poll_interval = _safe_float(
        cfg.get("poll_interval"), 3.0, minimum=0.5, maximum=60.0
    )
    max_wait = _safe_int(cfg.get("poll_max_wait"), 600, minimum=1, maximum=86400)
    deadline_poll = _now() + max_wait
    last = initial_response or {"task_id": task_id}
    while True:
        remaining = deadline_poll - _now()
        if remaining <= 0:
            status = _video_status(last)
            raise ApiException(f"视频生成超时(> {max_wait}s)，最后状态: {status}")
        try:
            request_timeout = max(1, min(timeout, int(remaining) + 1))
            last = await _get_json(poll_url, headers, request_timeout, proxy)
        except ApiException as e:
            if e.status in {400, 401, 403, 404, 405, 422}:
                raise
            # 偶发错误不致弃任务，继续重试
            if _now() > deadline_poll:
                raise
            await asyncio.sleep(max(poll_interval, 2))
            continue

        status = _video_status(last)
        status_norm = status.lower()
        if status_norm in ("success", "succeeded", "completed"):
            return last
        if status_norm in ("failed", "error", "cancelled", "canceled"):
            reason = _video_fail_reason(last)
            raise ApiException(f"视频生成失败: {reason or status}")
        await asyncio.sleep(poll_interval)


async def openai_video(
    cfg: dict,
    prompt: str,
    image_bytes: Optional[bytes] = None,
    image_filename: Optional[str] = None,
    timeout: int = 300,
    proxy: str = "",
) -> dict:
    """兼容入口：提交视频任务并在需要时轮询到结束。"""
    submit_resp, task_id = await submit_openai_video(
        cfg,
        prompt,
        image_bytes,
        image_filename,
        timeout,
        proxy,
    )
    if not task_id:
        return submit_resp
    return await poll_openai_video(cfg, task_id, submit_resp, timeout, proxy)


def _video_task_id(resp: dict) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    task_id = resp.get("task_id") or resp.get("id")
    data = resp.get("data")
    if not task_id and isinstance(data, dict):
        task_id = data.get("task_id") or data.get("id")
    return str(task_id) if task_id not in (None, "") else None


def _video_status(resp: dict) -> str:
    """从 newapi 视频轮询响应里提取任务状态字符串。

    注意：newapi 顶层 code 恒为 'success'(表示请求成功)，任务真实状态在 data.status，
    取值如 NOT_START / QUEUED / RUNNING / SUCCESS / FAILED。
    """
    if not isinstance(resp, dict):
        return ""
    data = resp.get("data")
    if isinstance(data, dict):
        s = data.get("status") or data.get("state")
        if s:
            return str(s)
    # 同步 OpenAI 风格 data[0].url 已带结果
    if isinstance(data, list) and data:
        return "SUCCESS"
    return str(resp.get("status") or resp.get("object") or "")


def _video_fail_reason(resp: dict) -> str:
    if isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict):
            return str(data.get("fail_reason") or data.get("error") or "")
        return str(resp.get("error") or "")
    return ""


def _now() -> float:
    import time as _t

    return _t.monotonic()


def _safe_int(value, default: int, minimum=None, maximum=None) -> int:
    try:
        result = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _safe_float(value, default: float, minimum=None, maximum=None) -> float:
    try:
        result = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result
