from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

from .models import CallerContext, Capability


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RateLimitLease:
    token: str
    scopes: list[str]


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


class PersistentRateLimiter:
    STATE_KEY = "imagegen_rate:state"

    def __init__(self, owner, config: dict | None = None):
        self.owner = owner
        self.config = config or {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, caller: CallerContext, capability: Capability
    ) -> RateLimitLease:
        async with self._lock:
            now = time.time()
            state = await self._get_state()
            self._prune(state, now)
            window = max(1, _as_int(self.config.get("window_seconds"), 3600))
            cost = max(
                1,
                _as_int(
                    self.config.get(f"{capability.value}_cost"),
                    3 if capability.media_kind == "video" else 1,
                ),
            )
            buckets = []
            if caller.sender_id:
                buckets.append(
                    (
                        f"user:{caller.sender_id}",
                        _as_int(self.config.get("user_limit"), 0),
                    )
                )
            if caller.group_id:
                buckets.append(
                    (
                        f"group:{caller.group_id}",
                        _as_int(self.config.get("group_limit"), 0),
                    )
                )
            for key, limit in buckets:
                if limit <= 0:
                    continue
                bucket = state["buckets"].get(key) or {
                    "window_start": now,
                    "used": 0,
                }
                if now - float(bucket.get("window_start", now)) >= window:
                    bucket = {"window_start": now, "used": 0}
                if int(bucket.get("used", 0)) + cost > limit:
                    retry_after = max(
                        1,
                        int(window - (now - float(bucket.get("window_start", now)))),
                    )
                    raise RateLimitExceeded(
                        f"已达到周期限流，请在约 {retry_after} 秒后重试。",
                        retry_after,
                    )
                state["buckets"][key] = bucket

            scopes = []
            concurrency_limits = []
            if caller.sender_id:
                concurrency_limits.append(
                    (
                        f"user:{caller.sender_id}",
                        _as_int(self.config.get("user_concurrency"), 0),
                    )
                )
            if caller.group_id:
                concurrency_limits.append(
                    (
                        f"group:{caller.group_id}",
                        _as_int(self.config.get("group_concurrency"), 0),
                    )
                )
            for key, limit in concurrency_limits:
                if limit <= 0:
                    continue
                active = state["leases"].get(key) or []
                if len(active) >= limit:
                    raise RateLimitExceeded("当前并发生成任务已达上限，请稍后重试。")
                scopes.append(key)

            for key, limit in buckets:
                if limit > 0:
                    state["buckets"][key]["used"] = (
                        int(state["buckets"][key].get("used", 0)) + cost
                    )
            token = secrets.token_hex(8)
            lease_ttl = max(
                60, _as_int(self.config.get("concurrency_lease_seconds"), 3600)
            )
            for key in scopes:
                state["leases"].setdefault(key, []).append(
                    {"token": token, "expires_at": now + lease_ttl}
                )
            await self._put_state(state)
            return RateLimitLease(token=token, scopes=scopes)

    async def release(self, lease: RateLimitLease | None):
        if not lease or not lease.scopes:
            return
        async with self._lock:
            state = await self._get_state()
            for key in lease.scopes:
                state["leases"][key] = [
                    item
                    for item in state["leases"].get(key, [])
                    if item.get("token") != lease.token
                ]
            await self._put_state(state)

    @staticmethod
    def _prune(state: dict, now: float):
        for key, values in list(state["leases"].items()):
            state["leases"][key] = [
                item for item in values if float(item.get("expires_at", 0)) > now
            ]

    async def _get_state(self) -> dict:
        method = getattr(self.owner, "get_kv_data", None)
        value = None
        if callable(method):
            try:
                value = await method(self.STATE_KEY, None)
            except Exception:
                logger.warning(
                    "Failed to read plugin KV key %s", self.STATE_KEY, exc_info=True
                )
                value = None
        if not isinstance(value, dict):
            value = {}
        return {
            "schema_version": 1,
            "buckets": dict(value.get("buckets") or {}),
            "leases": dict(value.get("leases") or {}),
        }

    async def _put_state(self, state: dict):
        method = getattr(self.owner, "put_kv_data", None)
        if callable(method):
            try:
                await method(self.STATE_KEY, state)
            except Exception:
                logger.warning(
                    "Failed to write plugin KV key %s", self.STATE_KEY, exc_info=True
                )


def _as_int(value, default=0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default
