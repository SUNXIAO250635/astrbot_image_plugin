"""四个接口适配器的请求封装。

每个适配器返回 dict(响应 JSON)；媒体提取统一交给 media.extract_media。
"""
from __future__ import annotations

import base64
from typing import Optional

import aiohttp


class ApiException(Exception):
    pass


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _join(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    # base_url 可能本身就是完整 endpoint，避免拼接出 //v1/...
    if base.endswith(path):
        return base
    return f"{base}{path}"


async def _get_json(
    url: str, headers: dict, timeout: int, proxy: str = ""
) -> dict:
    import json as _json

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {**headers, "Accept": "application/json"}
    proxy_kw = {"proxy": proxy} if proxy else {}
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.get(url, headers=headers, **proxy_kw) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(f"接口返回 {resp.status}: {text[:300]}")
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")


async def _post_json(
    url: str, headers: dict, payload: dict, timeout: int, proxy: str = ""
) -> dict:
    import json as _json

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
    proxy_kw = {"proxy": proxy} if proxy else {}
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.post(url, headers=headers, json=payload, **proxy_kw) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(f"接口返回 {resp.status}: {text[:300]}")
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")


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
    proxy_kw = {"proxy": proxy} if proxy else {}
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.post(url, headers=headers, data=form, **proxy_kw) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(f"接口返回 {resp.status}: {text[:300]}")
            try:
                return _json.loads(text)
            except Exception:
                raise ApiException(f"响应非 JSON: {text[:300]}")


# --------------------------------------------------------------------------- #
# 1) image-generation  /v1/images/generations
# --------------------------------------------------------------------------- #
async def image_generation(
    cfg: dict,
    prompt: str,
    timeout: int,
    image_bytes: Optional[bytes] = None,
    image_filename: Optional[str] = None,
    proxy: str = "",
) -> dict:
    model = cfg.get("model", "dall-e-3")
    url = _join(cfg["base_url"], "/v1/images/generations")
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
        payload["image"] = _image_data_uri(image_bytes, image_filename)
    return await _post_json(
        url, _auth_headers(cfg.get("api_key", "")), payload, timeout, proxy
    )


def _watermark_value(cfg: dict, model: str) -> Optional[bool]:
    raw = cfg.get("watermark", "false")
    if str(raw).strip().lower() in {"", "auto", "default", "none"}:
        return None
    return _as_bool(raw)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "开启", "启用"}


def _image_data_uri(image_bytes: bytes, image_filename: Optional[str] = None) -> str:
    filename = (image_filename or "").lower()
    mime = "image/png"
    if filename.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif filename.endswith(".webp"):
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


# --------------------------------------------------------------------------- #
# 2) image-edits  /v1/images/edits   (multipart)
# --------------------------------------------------------------------------- #
async def image_edits(
    cfg: dict,
    prompt: str,
    image_bytes: bytes,
    image_filename: str,
    timeout: int,
    proxy: str = "",
) -> dict:
    url = _join(cfg["base_url"], "/images/edits")
    # 部分 base_url 形如 https://api.x/v1 ，我们也补 /v1
    if "/v1" not in url:
        url = _join(cfg["base_url"], "/v1/images/edits")

    fields = [
        ("model", cfg.get("model", "gpt-image-1")),
        ("prompt", prompt),
        ("image", image_bytes, image_filename, "image/png"),
        ("n", str(cfg.get("n", 1) or 1)),
        ("size", cfg.get("size", "1024x1024")),
    ]
    return await _post_multipart(
        url, _auth_headers(cfg.get("api_key", "")), fields, timeout, proxy
    )


# --------------------------------------------------------------------------- #
# 3) openai  /v1/chat/completions
# --------------------------------------------------------------------------- #
async def openai_chat(
    cfg: dict,
    prompt: str,
    image_bytes: Optional[bytes] = None,
    timeout: int = 180,
    proxy: str = "",
) -> dict:
    url = _join(cfg["base_url"], "/v1/chat/completions")
    messages = []
    sysp = cfg.get("system_prompt", "")
    if sysp:
        messages.append({"role": "system", "content": sysp})

    if image_bytes is None:
        messages.append({"role": "user", "content": prompt})
    else:
        # 多模态：把图片作为 image_url 内联 base64
        import base64 as _b64

        b64 = _b64.b64encode(image_bytes).decode()
        data_uri = f"data:image/png;base64,{b64}"
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
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
async def openai_video(
    cfg: dict,
    prompt: str,
    image_bytes: Optional[bytes] = None,
    image_filename: Optional[str] = None,
    timeout: int = 300,
    proxy: str = "",
) -> dict:
    """文生视频 / 图生视频。

    newapi(渠道)风格：提交后返回 task_id，需要轮回 GET /v1/video/generations/{task_id}
    直到 data.status == SUCCESS 才有视频 URL。
    图生视频走 multipart(部分厂商接受 image 字段)。
    """
    import asyncio
    import base64 as _b64
    from aiohttp import FormData

    submit_url = _join(cfg["base_url"], "/v1/video/generations")
    headers = _auth_headers(cfg.get("api_key", ""))
    seconds = int(cfg.get("seconds", 8) or 8)

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
            form.add_field("image", f"data:image/png;base64,{b64}")
            form.add_field("image_file", image_bytes,
                           filename=image_filename or "input.png",
                           content_type="image/png")
        except Exception:
            form.add_field("image", image_bytes,
                           filename=image_filename or "input.png",
                           content_type="image/png")

        import json as _json

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        proxy_kw = {"proxy": proxy} if proxy else {}
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.post(submit_url, headers=headers, data=form,
                                    **proxy_kw) as resp:
                text = await resp.text(errors="ignore")
                if resp.status >= 400:
                    raise ApiException(f"接口返回 {resp.status}: {text[:300]}")
                try:
                    submit_resp = _json.loads(text)
                except Exception:
                    raise ApiException(f"响应非 JSON: {text[:300]}")

    # 提取 task_id；若提交响应里已经直接带视频/图片 URL，则视为同步完成。
    task_id = (
        submit_resp.get("task_id")
        if isinstance(submit_resp, dict) else None
    ) or (submit_resp.get("id") if isinstance(submit_resp, dict) else None)

    if not task_id:
        # 提交即完成(同步返回 media)，直接返回
        return submit_resp

    # 轮询：data.status 达到 SUCCESS/COMPLETED/video.success 即结束
    poll_url = f"{submit_url}/{task_id}"
    poll_interval = float(cfg.get("poll_interval", 3) or 3)
    max_wait = max(timeout, int(cfg.get("poll_max_wait", 600) or 600))
    deadline_poll = _now() + max_wait
    last = submit_resp
    while True:
        try:
            last = await _get_json(poll_url, headers, timeout, proxy)
        except ApiException as e:
            # 偶发错误不致弃任务，继续重试
            if _now() > deadline_poll:
                raise
            await asyncio.sleep(max(poll_interval, 2))
            continue

        status = _video_status(last)
        if status in ("SUCCESS", "COMPLETED", "succeeded", "success"):
            return last
        if status in ("FAILED", "failed", "error"):
            reason = _video_fail_reason(last)
            raise ApiException(f"视频生成失败: {reason or status}")
        if _now() > deadline_poll:
            raise ApiException(f"视频生成超时(> {max_wait}s)，最后状态: {status}")
        await asyncio.sleep(poll_interval)


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
