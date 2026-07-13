from __future__ import annotations

from dataclasses import dataclass

from .models import Capability
from .presets import detect_preset, get_preset


@dataclass(slots=True)
class IntentPlan:
    capability: Capability
    prompt: str
    preset: str = ""
    count: int = 1
    count_explicit: bool = False
    should_optimize: bool | None = None
    reason: str = "local"


class IntentPlanner:
    def local_plan(
        self,
        prompt: str,
        *,
        has_reference: bool = False,
        mode: str = "auto",
        preset: str = "",
        count: int | None = None,
    ) -> IntentPlan:
        normalized_mode = (mode or "auto").strip().lower()
        capability = _mode_capability(normalized_mode)
        text = (prompt or "").strip()
        selected_preset = get_preset(preset) or detect_preset(text)
        if capability is None:
            wants_video = any(
                token in text for token in ("视频", "动起来", "动画", "运镜")
            )
            edit_markers = (
                "改",
                "替换",
                "保留",
                "参考",
                "转换",
                "变成",
                "手办",
                "表情包",
            )
            if wants_video:
                capability = (
                    Capability.IMAGE_TO_VIDEO
                    if has_reference
                    else Capability.TEXT_TO_VIDEO
                )
            elif has_reference and (
                selected_preset
                and selected_preset.reference_preferred
                or any(marker in text for marker in edit_markers)
            ):
                capability = Capability.IMAGE_TO_IMAGE
            else:
                capability = Capability.TEXT_TO_IMAGE
        return IntentPlan(
            capability=capability,
            prompt=text,
            preset=selected_preset.key if selected_preset else "",
            count=max(1, min(10, int(count or 1))),
            count_explicit=count is not None,
        )

    def normalize_ai_plan(self, value: dict, fallback: IntentPlan) -> IntentPlan:
        if not isinstance(value, dict):
            return fallback
        try:
            capability = Capability(
                str(value.get("capability") or fallback.capability.value)
            )
        except ValueError:
            capability = fallback.capability
        preset = get_preset(str(value.get("preset") or ""))
        prompt = str(value.get("prompt") or fallback.prompt).strip() or fallback.prompt
        raw_count = value.get("count", value.get("image_count"))
        try:
            count = (
                max(1, min(10, int(raw_count)))
                if raw_count is not None
                else fallback.count
            )
        except (TypeError, ValueError):
            count = fallback.count
        explicit_count = _optional_bool(value.get("count_explicit"))
        if explicit_count is None:
            explicit_count = fallback.count_explicit or (
                raw_count is not None and count != 1
            )
        return IntentPlan(
            capability=capability,
            prompt=prompt,
            preset=preset.key if preset else fallback.preset,
            count=count,
            count_explicit=explicit_count,
            should_optimize=_optional_bool(value.get("should_optimize")),
            reason=str(value.get("reason") or "ai"),
        )


def _mode_capability(mode: str) -> Capability | None:
    aliases = {
        "text_to_image": Capability.TEXT_TO_IMAGE,
        "t2i": Capability.TEXT_TO_IMAGE,
        "文生图": Capability.TEXT_TO_IMAGE,
        "image_to_image": Capability.IMAGE_TO_IMAGE,
        "i2i": Capability.IMAGE_TO_IMAGE,
        "图生图": Capability.IMAGE_TO_IMAGE,
        "text_to_video": Capability.TEXT_TO_VIDEO,
        "t2v": Capability.TEXT_TO_VIDEO,
        "文生视频": Capability.TEXT_TO_VIDEO,
        "image_to_video": Capability.IMAGE_TO_VIDEO,
        "i2v": Capability.IMAGE_TO_VIDEO,
        "图生视频": Capability.IMAGE_TO_VIDEO,
    }
    return aliases.get(mode)


def _optional_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "是", "需要"}:
        return True
    if normalized in {"0", "false", "no", "off", "否", "不需要"}:
        return False
    return None
