from __future__ import annotations

import asyncio
import os
import time

import pytest

import main
from imagegen_core.cleanup import CleanupManager
from imagegen_core.models import CallerContext, Capability
from imagegen_core.policy import PersistentRateLimiter, RateLimitExceeded
from tests.fakes.runtime import FakeContext, FakeEvent, plugin_config


class KVOwner:
    def __init__(self):
        self.values = {}

    async def get_kv_data(self, key, default=None):
        return self.values.get(key, default)

    async def put_kv_data(self, key, value):
        self.values[key] = value


def test_rate_limit_persists_across_limiter_instances():
    async def scenario():
        owner = KVOwner()
        config = {"window_seconds": 3600, "user_limit": 2}
        caller = CallerContext(sender_id="user-1")
        first = PersistentRateLimiter(owner, config)
        second = PersistentRateLimiter(owner, config)

        await first.acquire(caller, Capability.TEXT_TO_IMAGE)
        await second.acquire(caller, Capability.TEXT_TO_IMAGE)
        with pytest.raises(RateLimitExceeded):
            await first.acquire(caller, Capability.TEXT_TO_IMAGE)

    asyncio.run(scenario())


def test_concurrency_lease_blocks_then_releases():
    async def scenario():
        owner = KVOwner()
        limiter = PersistentRateLimiter(owner, {"user_concurrency": 1})
        caller = CallerContext(sender_id="user-1")

        lease = await limiter.acquire(caller, Capability.TEXT_TO_VIDEO)
        with pytest.raises(RateLimitExceeded, match="并发"):
            await limiter.acquire(caller, Capability.TEXT_TO_VIDEO)
        await limiter.release(lease)
        await limiter.acquire(caller, Capability.TEXT_TO_VIDEO)

    asyncio.run(scenario())


def test_blacklist_takes_priority_over_whitelist():
    config = plugin_config()
    config["access_control"] = {
        "user_blacklist": "blocked",
        "user_whitelist": "blocked,allowed",
        "deny_message": "denied",
    }
    plugin = main.ImageGenPlugin(FakeContext(), config)

    result = plugin._access_denied_result(FakeEvent(sender_id="blocked"))

    assert result.kind == "plain"
    assert result.value == "denied"


def test_cleanup_removes_only_expired_managed_files(tmp_path):
    async def scenario():
        managed = tmp_path / "managed"
        managed.mkdir()
        expired = managed / "expired.png"
        active = managed / "active.png"
        fresh = managed / "fresh.png"
        outside = tmp_path / "outside.png"
        for path in (expired, active, fresh, outside):
            path.write_bytes(b"x")
        old = time.time() - 3600
        os.utime(expired, (old, old))
        os.utime(active, (old, old))
        os.utime(outside, (old, old))
        cleaner = CleanupManager(
            str(managed), ttl_seconds=60, interval_seconds=60
        )

        removed = await cleaner.run_once([str(active)])

        assert str(expired.resolve()) in removed
        assert not expired.exists()
        assert active.exists()
        assert fresh.exists()
        assert outside.exists()

    asyncio.run(scenario())


def test_last_image_index_restores_from_plugin_kv(tmp_path):
    async def scenario():
        image_path = tmp_path / "cached.png"
        image_path.write_bytes(b"cached-image")
        context = FakeContext()
        event = FakeEvent()
        first = main.ImageGenPlugin(context, plugin_config())
        first._remember_last_image(event, str(image_path), "cached.png")
        await asyncio.sleep(0)

        second = main.ImageGenPlugin(context, plugin_config())
        items = await second._get_cached_image_items(event)

        assert items[0]["bytes"] == b"cached-image"
        assert items[0]["filename"] == "cached.png"

    asyncio.run(scenario())
