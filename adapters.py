"""四个接口适配器的请求封装。

每个适配器返回 dict(响应 JSON)；媒体提取统一交给 media.extract_media。
"""
from __future__ import annotations

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


async def _post_json(
    url: str, headers: dict, payload: dict, timeout: int
) -> dict:
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {**headers, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text(errors="ignore")
            if resp.status >= 400:
                raise ApiException(f"接口返回 {resp.status}: {text[:300]}")
            import json as _json

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
    cfg: dict, prompt: str, timeout: int
) -> dict:
    url = _join(cfg["base_url"], "/v1/images/generations")
    payload = {
        "model": cfg.get("model", "dall-e-3"),
        "prompt": prompt,
        "n": int(cfg.get("n", 1) or 1),
        "size": cfg.get("size", "1024x1024"),
    }
    return await _post_json(url, _auth_headers(cfg.get("api_key", "")), payload, timeout)


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
    cfg: dict, prompt: str, image_bytes: Optional[bytes] = None, timeout: int = 180
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
    return await _post_json(url, _auth_headers(cfg.get("api_key", "")), payload, timeout)


# --------------------------------------------------------------------------- #
# 4) openai-video  /v1/video/generations
# --------------------------------------------------------------------------- #
async def openai_video(
    cfg: dict,
    prompt: str,
    image_bytes: Optional[bytes] = None,
    image_filename: Optional[str] = None,
    timeout: int = 180,
    proxy: str = "",
) -> dict:
    """文生视频 / 图生视频。

    文生视频走 JSON；若附带图片则用 multipart(部分厂商接受 image 字段)。
    """
    url = _join(cfg["base_url"], "/v1/video/generations")

    headers = _auth_headers(cfg.get("api_key", ""))

    if image_bytes is None:
        payload = {
            "model": cfg.get("model", "sora"),
            "prompt": prompt,
            "seconds": int(cfg.get("seconds", 8) or 8),
        }
        return await _post_json(url, headers, payload, timeout)

    # 图生视频：multipart，带 image
    from aiohttp import FormData
    import json as _json

    form = FormData()
    form.add_field("model", cfg.get("model", "sora"))
    form.add_field("prompt", prompt)
    form.add_field("seconds", str(cfg.get("seconds", 8) or 8))
    form.add_field("image", image_bytes, filename=image_filename or "input.png",
                   content_type="image/png")
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
