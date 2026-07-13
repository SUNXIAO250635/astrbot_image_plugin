from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from .models import ReferenceAsset


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
LoadImage = Callable[[str, str], Awaitable[tuple[bytes | None, str | None]]]
CachedReference = Callable[[], Awaitable[list[dict]]]


@dataclass(slots=True)
class ReferenceCandidate:
    value: str
    filename: str
    source: str
    owner_id: str = ""


class ReferenceResolver:
    def __init__(self, load_image: LoadImage):
        self.load_image = load_image

    async def resolve(
        self,
        event,
        prompt: str = "",
        *,
        max_images: int = 4,
        cached_loader: CachedReference | None = None,
        allow_cached: bool = False,
    ) -> list[ReferenceAsset]:
        candidates = self._extract_chain(event, "current")
        candidates.extend(await self._extract_platform_references(event))
        candidates.extend(self._avatar_candidates(event, prompt))
        candidates = _deduplicate(candidates)
        if not candidates and allow_cached and cached_loader:
            cached = await cached_loader()
            candidates.extend(
                ReferenceCandidate(
                    value=str(item.get("ref") or item.get("value") or ""),
                    filename=str(item.get("filename") or "input.png"),
                    source="cache",
                )
                for item in cached
                if item.get("ref") or item.get("value")
            )

        owner_id = _sender_id(event)
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        assets = []
        for index, candidate in enumerate(candidates[: max(1, max_images)], start=1):
            data, filename = await self.load_image(candidate.value, candidate.filename)
            if not data:
                continue
            assets.append(
                ReferenceAsset(
                    index=index,
                    source=candidate.source,
                    value=candidate.value,
                    filename=filename or candidate.filename,
                    mime_type=_mime_type(filename or candidate.filename),
                    data=data,
                    owner_id=candidate.owner_id or owner_id,
                    session_id=session_id,
                )
            )
        return assets

    def _extract_chain(self, event, source: str) -> list[ReferenceCandidate]:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None) or []
        return self._walk(chain, source)

    def _walk(self, value, source: str, seen=None) -> list[ReferenceCandidate]:
        seen = seen or set()
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            return []
        identity = id(value)
        if identity in seen:
            return []
        seen.add(identity)
        if isinstance(value, dict):
            candidates = []
            direct = _candidate_from_mapping(value, source)
            if direct:
                candidates.append(direct)
            for key in ("message", "messages", "content", "chain", "nodes", "data"):
                nested = value.get(key)
                if nested is not None:
                    candidates.extend(self._walk(nested, _nested_source(source, key), seen))
            return candidates
        if isinstance(value, (list, tuple, set)):
            candidates = []
            for item in value:
                candidates.extend(self._walk(item, source, seen))
            return candidates

        candidates = []
        direct = _candidate_from_component(value, source)
        if direct:
            candidates.append(direct)
        for attr in (
            "message",
            "messages",
            "content",
            "chain",
            "message_chain",
            "nodes",
            "data",
        ):
            nested = getattr(value, attr, None)
            if nested is not None and nested is not value:
                candidates.extend(self._walk(nested, _nested_source(source, attr), seen))
        return candidates

    async def _extract_platform_references(self, event) -> list[ReferenceCandidate]:
        candidates = []
        reply_ids, forward_ids = _component_ids(event)
        for reply_id in reply_ids:
            payload = await _fetch_payload(event, "reply", reply_id)
            candidates.extend(self._walk(payload, "reply"))
        for forward_id in forward_ids:
            payload = await _fetch_payload(event, "forward", forward_id)
            candidates.extend(self._walk(payload, "forward"))
        for file_id, busid, filename in _group_file_ids(event):
            url = await _fetch_group_file_url(event, file_id, busid)
            if url:
                candidates.append(
                    ReferenceCandidate(
                        value=url,
                        filename=filename or "group_file.png",
                        source="group_file",
                    )
                )
        return candidates

    def _avatar_candidates(self, event, prompt: str) -> list[ReferenceCandidate]:
        if "头像" not in (prompt or ""):
            return []
        targets = _at_targets(event)
        if any(marker in prompt for marker in ("我的头像", "我头像", "本人头像")):
            targets.insert(0, _sender_id(event))
        candidates = []
        for target in targets:
            if target and str(target).isdigit():
                candidates.append(
                    ReferenceCandidate(
                        value=f"https://q1.qlogo.cn/g?b=qq&nk={target}&s=640",
                        filename=f"avatar_{target}.jpg",
                        source="avatar",
                        owner_id=str(target),
                    )
                )
        return candidates


def _candidate_from_component(component, source: str):
    name = type(component).__name__.lower()
    value = (
        getattr(component, "url", None)
        or getattr(component, "file", None)
        or getattr(component, "path", None)
    )
    filename = (
        getattr(component, "filename", None)
        or getattr(component, "name", None)
        or _filename(str(value or ""))
    )
    is_image = "image" in name or "photo" in name
    is_file_image = "file" in name and str(filename).lower().endswith(IMAGE_EXTENSIONS)
    if value and (is_image or is_file_image or _looks_like_image(str(value))):
        return ReferenceCandidate(str(value), str(filename or "input.png"), source)
    return None


def _candidate_from_mapping(value: dict, source: str):
    media_type = str(value.get("type") or value.get("post_type") or "").lower()
    nested_data = value.get("data") if isinstance(value.get("data"), dict) else {}
    raw = (
        value.get("url")
        or value.get("file")
        or value.get("path")
        or nested_data.get("url")
        or nested_data.get("file")
        or nested_data.get("path")
    )
    filename = (
        value.get("filename")
        or value.get("name")
        or nested_data.get("filename")
        or nested_data.get("name")
        or _filename(str(raw or ""))
    )
    if raw and (
        "image" in media_type
        or "photo" in media_type
        or str(filename).lower().endswith(IMAGE_EXTENSIONS)
        or _looks_like_image(str(raw))
    ):
        return ReferenceCandidate(str(raw), str(filename or "input.png"), source)
    return None


async def _fetch_payload(event, kind: str, identifier: str):
    method_names = (
        ("get_reply_message", "get_message", "get_msg")
        if kind == "reply"
        else ("get_forward_message", "get_forward_msg")
    )
    objects = [
        event,
        getattr(event, "bot", None),
        getattr(getattr(event, "message_obj", None), "bot", None),
    ]
    for obj in objects:
        if obj is None:
            continue
        for method_name in method_names:
            method = getattr(obj, method_name, None)
            if not callable(method):
                continue
            for kwargs in (
                {"message_id": identifier},
                {"id": identifier},
                {"forward_id": identifier},
            ):
                try:
                    result = method(**kwargs)
                    return await result if inspect.isawaitable(result) else result
                except TypeError:
                    continue
                except Exception:
                    break
    return None


def _component_ids(event) -> tuple[list[str], list[str]]:
    chain = getattr(getattr(event, "message_obj", None), "message", None) or []
    reply_ids, forward_ids = [], []
    for component in chain:
        name = type(component).__name__.lower()
        identifier = (
            getattr(component, "id", None)
            or getattr(component, "message_id", None)
            or getattr(component, "file", None)
        )
        if identifier and any(token in name for token in ("reply", "quote")):
            reply_ids.append(str(identifier))
        if identifier and "forward" in name:
            forward_ids.append(str(identifier))
    return reply_ids, forward_ids


def _group_file_ids(event) -> list[tuple[str, str, str]]:
    result = []
    chain = getattr(getattr(event, "message_obj", None), "message", None) or []
    for component in chain:
        name = type(component).__name__.lower()
        if "file" not in name or "image" in name:
            continue
        direct = (
            getattr(component, "url", None)
            or getattr(component, "path", None)
            or getattr(component, "file", None)
        )
        filename = str(
            getattr(component, "filename", None)
            or getattr(component, "name", None)
            or ""
        )
        if direct and (
            _looks_like_image(str(direct))
            or filename.lower().endswith(IMAGE_EXTENSIONS)
        ):
            continue
        file_id = getattr(component, "file_id", None) or getattr(component, "id", None)
        busid = getattr(component, "busid", None) or getattr(component, "bus_id", None)
        if file_id and filename.lower().endswith(IMAGE_EXTENSIONS):
            result.append((str(file_id), str(busid or ""), filename))
    return result


async def _fetch_group_file_url(event, file_id: str, busid: str) -> str:
    group_id = ""
    try:
        group_id = str(event.get_group_id() or "")
    except Exception:
        group_id = str(getattr(event, "group_id", "") or "")
    objects = [
        event,
        getattr(event, "bot", None),
        getattr(getattr(event, "message_obj", None), "bot", None),
    ]
    for obj in objects:
        method = getattr(obj, "get_group_file_url", None) if obj else None
        if not callable(method):
            continue
        try:
            result = method(group_id=group_id, file_id=file_id, busid=busid)
            result = await result if inspect.isawaitable(result) else result
        except Exception:
            continue
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            url = data.get("url") if isinstance(data, dict) else None
            if url:
                return str(url)
    return ""


def _at_targets(event) -> list[str]:
    targets = []
    chain = getattr(getattr(event, "message_obj", None), "message", None) or []
    for component in chain:
        if "at" not in type(component).__name__.lower():
            continue
        target = (
            getattr(component, "qq", None)
            or getattr(component, "target", None)
            or getattr(component, "user_id", None)
            or getattr(component, "id", None)
        )
        if target not in (None, ""):
            targets.append(str(target))
    return targets


def _sender_id(event) -> str:
    try:
        return str(event.get_sender_id() or "")
    except Exception:
        return str(getattr(event, "sender_id", "") or "")


def _deduplicate(candidates: list[ReferenceCandidate]) -> list[ReferenceCandidate]:
    result, seen = [], set()
    for candidate in candidates:
        if not candidate.value or candidate.value in seen:
            continue
        seen.add(candidate.value)
        result.append(candidate)
    return result


def _nested_source(source: str, key: str) -> str:
    if source in {"reply", "forward"}:
        return source
    return "forward" if key in {"nodes", "messages"} else source


def _looks_like_image(value: str) -> bool:
    lower = value.lower()
    return lower.startswith(("base64://", "data:image/")) or any(
        lower.split("?", 1)[0].endswith(ext) for ext in IMAGE_EXTENSIONS
    )


def _filename(value: str) -> str:
    if not value or value.startswith(("base64://", "data:")):
        return "input.png"
    if value.startswith("http"):
        return os.path.basename(urlparse(value).path) or "input.png"
    if value.startswith("file://"):
        return os.path.basename(unquote(urlparse(value).path)) or "input.png"
    return os.path.basename(value) or "input.png"


def _mime_type(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"
