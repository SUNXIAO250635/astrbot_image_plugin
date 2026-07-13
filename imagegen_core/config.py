from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Capability


CAPABILITY_ALIASES = {
    "text_to_image": Capability.TEXT_TO_IMAGE,
    "txt2img": Capability.TEXT_TO_IMAGE,
    "t2i": Capability.TEXT_TO_IMAGE,
    "image_to_image": Capability.IMAGE_TO_IMAGE,
    "img2img": Capability.IMAGE_TO_IMAGE,
    "i2i": Capability.IMAGE_TO_IMAGE,
    "text_to_video": Capability.TEXT_TO_VIDEO,
    "txt2video": Capability.TEXT_TO_VIDEO,
    "t2v": Capability.TEXT_TO_VIDEO,
    "image_to_video": Capability.IMAGE_TO_VIDEO,
    "img2video": Capability.IMAGE_TO_VIDEO,
    "i2v": Capability.IMAGE_TO_VIDEO,
}


@dataclass(slots=True)
class ProviderProfile:
    provider_id: str
    provider_type: str
    config: dict[str, Any]
    capabilities: frozenset[Capability]
    enabled: bool = True
    priority: int = 0
    position: int = 0
    legacy: bool = False


@dataclass(slots=True)
class RoutingConfig:
    mode: str = "ordered_failover"
    orders: dict[Capability, list[str]] = field(default_factory=dict)
    failure_threshold: int = 2
    cooldown_seconds: float = 30.0
    max_attempts: int = 0
    failover_after_terminal_failure: bool = False


def split_values(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip() for item in re.split(r"[\s,，;；]+", str(value)) if item.strip()
    ]


def parse_capabilities(value, provider_type: str = "") -> frozenset[Capability]:
    items = split_values(value)
    unknown = [item for item in items if item.lower() not in CAPABILITY_ALIASES]
    if unknown:
        raise ValueError(f"未知 capabilities: {', '.join(unknown)}")
    parsed = {CAPABILITY_ALIASES[item.lower()] for item in items}
    if parsed:
        return frozenset(parsed)
    if provider_type in {"openai_images", "xai", "gemini", "google_gemini"}:
        return frozenset({Capability.TEXT_TO_IMAGE, Capability.IMAGE_TO_IMAGE})
    return frozenset(Capability)


def provider_profiles(config: dict) -> list[ProviderProfile]:
    configured = config.get("providers") or []
    if isinstance(configured, list) and configured:
        profiles = []
        seen_ids = set()
        for position, raw in enumerate(configured):
            if not isinstance(raw, dict):
                continue
            provider_type = str(
                raw.get("provider_type") or raw.get("__template_key") or "openai_compat"
            ).strip()
            protocol = str(raw.get("protocol") or "").strip().lower()
            if protocol and protocol not in {
                "openai_compat",
                "gemini",
                "generic_json",
            }:
                raise ValueError(f"未知 provider protocol: {protocol}")
            provider_id = str(
                raw.get("provider_id") or raw.get("id") or f"provider-{position + 1}"
            ).strip()
            if provider_id in seen_ids:
                raise ValueError(f"providers 中存在重复 provider_id: {provider_id}")
            seen_ids.add(provider_id)
            profiles.append(
                ProviderProfile(
                    provider_id=provider_id,
                    provider_type=provider_type,
                    config=dict(raw),
                    capabilities=parse_capabilities(
                        raw.get("capabilities"), provider_type
                    ),
                    enabled=_as_bool(raw.get("enabled", True), True),
                    priority=_as_int(raw.get("priority"), 0),
                    position=position,
                )
            )
        return profiles
    return _legacy_profiles(config)


def routing_config(config: dict) -> RoutingConfig:
    raw = config.get("routing") or {}
    orders = {
        capability: split_values(raw.get(f"{capability.value}_order"))
        for capability in Capability
    }
    if not any(orders.values()):
        orders = _legacy_orders(config)
    return RoutingConfig(
        mode=str(raw.get("mode") or "ordered_failover").strip().lower(),
        orders=orders,
        failure_threshold=max(1, _as_int(raw.get("failure_threshold"), 2)),
        cooldown_seconds=max(0.0, _as_float(raw.get("cooldown_seconds"), 30.0)),
        max_attempts=max(0, _as_int(raw.get("max_attempts"), 0)),
        failover_after_terminal_failure=_as_bool(
            raw.get("failover_after_terminal_failure"), False
        ),
    )


def _legacy_profiles(config: dict) -> list[ProviderProfile]:
    options = config.get("generation_options") or {}
    image_strategy = options.get("image_to_image_strategy", "image_edits")
    text_video_strategy = options.get("video_via_strategy", "openai_video")
    image_video_strategy = options.get("image_to_video_strategy", "openai_video")
    definitions = [
        (
            "legacy_image_generation",
            "openai_images",
            "adapter_image_generation",
            {Capability.TEXT_TO_IMAGE}
            | (
                {Capability.IMAGE_TO_IMAGE}
                if image_strategy == "image_generation"
                else set()
            ),
            {"image_api": "generation"},
        ),
        (
            "legacy_image_edits",
            "openai_images",
            "adapter_image_edits",
            ({Capability.IMAGE_TO_IMAGE} if image_strategy == "image_edits" else set())
            | (
                {Capability.IMAGE_TO_VIDEO}
                if image_video_strategy == "image_edits"
                else set()
            ),
            {"image_api": "edits", "video_api": "edits"},
        ),
        (
            "legacy_openai_chat",
            "openai_compat",
            "adapter_openai_chat",
            {Capability.TEXT_TO_VIDEO}
            if text_video_strategy == "openai_chat"
            else set(),
            {"video_api": "chat"},
        ),
        (
            "legacy_openai_video",
            "openai_compat",
            "adapter_openai_video",
            (
                {Capability.TEXT_TO_VIDEO}
                if text_video_strategy == "openai_video"
                else set()
            )
            | (
                {Capability.IMAGE_TO_VIDEO}
                if image_video_strategy == "openai_video"
                else set()
            ),
            {"video_api": "video"},
        ),
    ]
    profiles = []
    for position, (
        provider_id,
        provider_type,
        key,
        capabilities,
        defaults,
    ) in enumerate(definitions):
        if not capabilities:
            continue
        profile_config = {**defaults, **dict(config.get(key) or {})}
        profile_config["timeout"] = (config.get("media") or {}).get("timeout", 300)
        profile_config["proxy"] = (config.get("media") or {}).get("proxy", "")
        profiles.append(
            ProviderProfile(
                provider_id=provider_id,
                provider_type=provider_type,
                config=profile_config,
                capabilities=frozenset(capabilities),
                priority=100 - position,
                position=position,
                legacy=True,
            )
        )
    return profiles


def _legacy_orders(config: dict) -> dict[Capability, list[str]]:
    options = config.get("generation_options") or {}
    image_provider = (
        "legacy_image_generation"
        if options.get("image_to_image_strategy", "image_edits") == "image_generation"
        else "legacy_image_edits"
    )
    text_video_provider = (
        "legacy_openai_chat"
        if options.get("video_via_strategy", "openai_video") == "openai_chat"
        else "legacy_openai_video"
    )
    image_video_provider = (
        "legacy_image_edits"
        if options.get("image_to_video_strategy", "openai_video") == "image_edits"
        else "legacy_openai_video"
    )
    return {
        Capability.TEXT_TO_IMAGE: ["legacy_image_generation"],
        Capability.IMAGE_TO_IMAGE: [image_provider],
        Capability.TEXT_TO_VIDEO: [text_video_provider],
        Capability.IMAGE_TO_VIDEO: [image_video_provider],
    }


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "启用"}


def _as_int(value, default=0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default
