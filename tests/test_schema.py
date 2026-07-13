from __future__ import annotations

import json
from pathlib import Path


SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
        encoding="utf-8"
    )
)


def _items(section: str) -> dict:
    return SCHEMA[section]["items"]


def test_webui_exposes_prompt_chat_connection_and_model_fields():
    assert {"base_url", "api_key", "model"} <= set(_items("adapter_prompt_chat"))
    assert {"base_url", "api_key", "model"} <= set(_items("adapter_openai_chat"))


def test_webui_exposes_image_adapter_and_semantic_planning_fields():
    generation = _items("generation_options")
    assert {
        "image_to_image_strategy",
        "prompt_enhance_enabled",
        "intent_plan_enabled",
        "image_edit_plan_enabled",
        "image_edit_plan_send_images",
        "image_edit_max_images",
    } <= set(generation)
    assert {"base_url", "api_key", "model", "watermark"} <= set(
        _items("adapter_image_generation")
    )
    assert {"base_url", "api_key", "model"} <= set(
        _items("adapter_image_edits")
    )


def test_webui_exposes_access_provider_and_meme_fields():
    assert {
        "user_whitelist",
        "group_whitelist",
        "user_blacklist",
        "group_blacklist",
    } <= set(_items("access_control"))
    provider = SCHEMA["providers"]["templates"]["provider"]["items"]
    assert {
        "provider_id",
        "provider_type",
        "protocol",
        "base_url",
        "api_key",
        "model",
        "capabilities",
        "priority",
    } <= set(provider)
    assert {
        "enabled",
        "adaptive_enabled",
        "vision_enabled",
        "minimum_slices",
        "expected_slices",
        "grid_rows",
        "grid_columns",
    } <= set(_items("meme_splitter"))


def test_schema_defaults_do_not_embed_credentials():
    serialized = json.dumps(SCHEMA, ensure_ascii=False).lower()
    assert "sk-" not in serialized
    assert _items("adapter_prompt_chat")["api_key"]["default"] == ""
