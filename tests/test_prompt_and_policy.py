from __future__ import annotations

import asyncio

import main
from tests.fakes.runtime import FakeContext, FakeEvent, plugin_config


def test_no_optimize_prompt_keeps_semantics_and_extracts_three_images():
    async def scenario():
        config = plugin_config()
        config["generation_options"]["prompt_enhance_enabled"] = True
        plugin = main.ImageGenPlugin(FakeContext(), config)

        prompt, notice, count = await plugin._prepare_image_prompt(
            "画一个红烧肉，给我三版方案 不要优化", "文生图"
        )

        assert prompt == "画一个红烧肉"
        assert notice is None
        assert count == 3

    asyncio.run(scenario())


def test_output_count_parser_handles_chinese_and_numeric_requests():
    assert main.ImageGenPlugin._requested_output_count("给我三版方案") == 3
    assert main.ImageGenPlugin._requested_output_count("生成 4 张城市夜景") == 4
    assert main.ImageGenPlugin._requested_output_count("画一只猫") is None


def test_empty_whitelists_do_not_restrict_access():
    plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
    event = FakeEvent(sender_id="any-user", group_id="any-group")

    assert plugin._access_denied_result(event) is None


def test_user_and_group_whitelists_are_both_enforced():
    config = plugin_config()
    config["access_control"] = {
        "user_whitelist": "allowed-user",
        "group_whitelist": "allowed-group",
        "deny_message": "denied",
    }
    plugin = main.ImageGenPlugin(FakeContext(), config)

    assert plugin._access_denied_result(
        FakeEvent(sender_id="allowed-user", group_id="allowed-group")
    ) is None
    denied_user = plugin._access_denied_result(
        FakeEvent(sender_id="other-user", group_id="allowed-group")
    )
    denied_group = plugin._access_denied_result(
        FakeEvent(sender_id="allowed-user", group_id="other-group")
    )
    assert denied_user.kind == "plain"
    assert denied_user.value == "denied"
    assert denied_group.kind == "plain"
    assert denied_group.value == "denied"


def test_previous_image_cache_is_isolated_by_session_and_user():
    plugin = main.ImageGenPlugin(FakeContext(), plugin_config())
    first = FakeEvent(
        sender_id="user-1",
        group_id="group-1",
        unified_msg_origin="test:group:group-1",
    )
    same_user = FakeEvent(
        sender_id="user-1",
        group_id="group-1",
        unified_msg_origin="test:group:group-1",
    )
    other_user = FakeEvent(
        sender_id="user-2",
        group_id="group-1",
        unified_msg_origin="test:group:group-1",
    )

    plugin._remember_last_image(first, "https://cdn.invalid/first.png")

    assert plugin._get_cached_image_ref(same_user)[0].endswith("first.png")
    assert plugin._get_cached_image_ref(other_user) == (None, None)
