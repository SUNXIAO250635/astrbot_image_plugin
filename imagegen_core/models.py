from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"

    @property
    def media_kind(self) -> str:
        return "video" if self in {
            Capability.TEXT_TO_VIDEO,
            Capability.IMAGE_TO_VIDEO,
        } else "image"

    @property
    def needs_reference(self) -> bool:
        return self in {
            Capability.IMAGE_TO_IMAGE,
            Capability.IMAGE_TO_VIDEO,
        }


class ErrorKind(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVER = "server"
    UNSUPPORTED = "unsupported"
    PARSE = "parse"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class CallerContext:
    unified_msg_origin: str = ""
    sender_id: str = ""
    group_id: str = ""
    platform: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "unified_msg_origin": self.unified_msg_origin,
            "sender_id": self.sender_id,
            "group_id": self.group_id,
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "CallerContext":
        value = value or {}
        return cls(
            unified_msg_origin=str(value.get("unified_msg_origin") or ""),
            sender_id=str(value.get("sender_id") or ""),
            group_id=str(value.get("group_id") or ""),
            platform=str(value.get("platform") or ""),
        )


@dataclass(slots=True)
class ReferenceAsset:
    index: int
    source: str
    value: str = ""
    filename: str = "input.png"
    mime_type: str = "image/png"
    data: bytes | None = None
    owner_id: str = ""
    session_id: str = ""
    temporary: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source": self.source,
            "value": self.value,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "temporary": self.temporary,
        }


@dataclass(slots=True)
class MediaArtifact:
    kind: str
    value: str
    mime_type: str = ""
    provider_id: str = ""
    temporary: bool = False
    expires_at: float | None = None

    def as_response_item(self) -> dict | str:
        if self.value.startswith("data:"):
            return self.value
        key = "video_url" if self.kind == "video" else "url"
        return {key: self.value}


@dataclass(slots=True)
class GenerationRequest:
    capability: Capability
    prompt: str
    references: list[ReferenceAsset] = field(default_factory=list)
    count: int = 1
    size: str = ""
    aspect_ratio: str = ""
    duration: int | None = None
    preset: str = ""
    caller: CallerContext = field(default_factory=CallerContext)
    job_id: str = ""
    provider_hint: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.prompt = (self.prompt or "").strip()
        try:
            self.count = max(1, min(10, int(self.count or 1)))
        except (TypeError, ValueError):
            self.count = 1


@dataclass(slots=True)
class GenerationHandle:
    provider_id: str
    remote_task_id: str
    capability: Capability
    accepted_at: float
    poll_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "remote_task_id": self.remote_task_id,
            "capability": self.capability.value,
            "accepted_at": self.accepted_at,
            "poll_metadata": self.poll_metadata,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "GenerationHandle":
        return cls(
            provider_id=str(value.get("provider_id") or ""),
            remote_task_id=str(value.get("remote_task_id") or ""),
            capability=Capability(value.get("capability")),
            accepted_at=float(value.get("accepted_at") or 0),
            poll_metadata=dict(value.get("poll_metadata") or {}),
        )


@dataclass(slots=True)
class GenerationResult:
    provider_id: str
    media: list[MediaArtifact] = field(default_factory=list)
    remote_task_id: str = ""
    elapsed_seconds: float = 0.0
    attempts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None

    def as_response(self) -> dict[str, list[dict | str]]:
        return {"data": [artifact.as_response_item() for artifact in self.media]}


class ProviderFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind = ErrorKind.UNKNOWN,
        provider_id: str = "",
        status: int | None = None,
        retryable: bool = False,
        accepted: bool = False,
        remote_task_id: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.provider_id = provider_id
        self.status = status
        self.retryable = retryable
        self.accepted = accepted
        self.remote_task_id = remote_task_id
