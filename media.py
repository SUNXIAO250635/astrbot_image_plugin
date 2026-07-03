"""媒体处理：解析接口返回、下载与保存到 data 目录。"""
from __future__ import annotations

import base64
import os
import re
from typing import Optional, Tuple

import aiohttp

# 媒体类型
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi")

_MEDIA_RE = re.compile(r"https?://[^\s\)\"'<>]+", re.IGNORECASE)
_BASE64_RE = re.compile(
    r"data:(image|video)/[\w+.\-]+;base64,([A-Za-z0-9+/=]+)"
)


def _ext_from_url(url: str) -> str:
    """从 URL 中提取扩展名（小写带点），取不到时返回空串。"""
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:$|\?|#)", url)
    if m:
        return "." + m.group(1).lower()
    return ""


def _kind_from_url(url: str) -> str:
    ext = _ext_from_url(url)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    # 无法判定时默认按图片处理
    return "image"


def extract_media(resp_json: dict) -> Optional[Tuple[str, str]]:
    """从各种接口的 JSON 响应里提取 (kind, value)。

    value 可以是 http(s) URL 或 data: URI(base64)。
    兜底覆盖 OpenAI images / image-edits / video / chat completions 几种常见结构。
    """
    if not isinstance(resp_json, dict):
        return None

    # 1) data: [...] 数组（OpenAI images / video 风格）
    data = resp_json.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("video_url") or item.get("image_url")
            if url:
                return _kind_from_url(url), url
            b64 = item.get("b64_json") or item.get("b64")
            if b64:
                return "image", f"data:image/png;base64,{b64}"
            # 有些接口直接 data[0] = url(string)
            if isinstance(item, str) and item.startswith("http"):
                return _kind_from_url(item), item

    # 2) 顶层 url / video_url / image_url
    for key in ("video_url", "image_url", "url", "video", "output"):
        v = resp_json.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return _kind_from_url(v), v
        if isinstance(v, str) and "base64" in v:
            m = _BASE64_RE.match(v)
            if m:
                return ("image" if m.group(1) == "image" else "video"), v

    # 3) chat completions：从 choices[0].message.content 文本里提取 URL
    choices = resp_json.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            return _extract_from_text(content)

    return None


def _extract_from_text(text: str) -> Optional[Tuple[str, str]]:
    """从 chat 回复文本中提取第一个媒体 URL 或 base64。"""
    m = _BASE64_RE.search(text)
    if m:
        return ("image" if m.group(1) == "image" else "video"), m.group(0)
    for url in _MEDIA_RE.findall(text):
        # markdown 链接里可能尾随 ) ，清理
        url = url.rstrip(",);")
        if any(url.lower().endswith(e) for e in VIDEO_EXTS):
            return "video", url
    for url in _MEDIA_RE.findall(text):
        url = url.rstrip(",);")
        if any(url.lower().endswith(e) for e in IMAGE_EXTS):
            return "image", url
    # 退而求其次：任意 http 链接都按图片
    for url in _MEDIA_RE.findall(text):
        url = url.rstrip(",);")
        if url.startswith("http"):
            return "image", url
    return None


async def download_to_file(
    value: str,
    save_dir: str,
    filename_stem: str,
    timeout: int = 180,
    proxy: str = "",
) -> Tuple[str, str]:
    """把返回的 URL 或 base64 保存成本地文件，返回 (kind, abs_path)。

    value: http(s) URL 或 data: URI 或本地 file 路径。
    """
    os.makedirs(save_dir, exist_ok=True)

    # data: URI
    if value.startswith("data:"):
        m = _BASE64_RE.match(value)
        if not m:
            raise ValueError("无法解析 base64 媒体")
        kind = "image" if m.group(1) == "image" else "video"
        ext = ".png" if kind == "image" else ".mp4"
        path = os.path.join(save_dir, f"{filename_stem}{ext}")
        with open(path, "wb") as f:
            f.write(base64.b64decode(m.group(2)))
        return kind, path

    # 本地路径直接返回
    if os.path.exists(value):
        kind = "video" if _ext_from_url(value) in VIDEO_EXTS else "image"
        return kind, value

    # HTTP 下载
    want_video = _kind_from_url(value) == "video"
    if not ext:
        ext = ".mp4" if want_video else ".png"
    path = os.path.join(save_dir, f"{filename_stem}{ext}")
    headers = {
        "User-Agent": "astrbot_plugin_imagegen/0.1",
    }
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    proxy_kw = {"proxy": proxy} if proxy else {}
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.get(value, headers=headers, **proxy_kw) as resp:
            if resp.status != 200:
                body = await resp.text(errors="ignore")
                raise RuntimeError(f"下载失败 {resp.status}: {body[:200]}")
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1 << 16):
                    f.write(chunk)
    kind = "video" if ext in VIDEO_EXTS else "image"
    return kind, path
