from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .models import GenerationHandle


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JobRun:
    job_id: str
    background: bool
    result: Any = None


class JobManager:
    INDEX_KEY = "imagegen_jobs:index"

    def __init__(
        self,
        owner,
        *,
        foreground_wait_seconds: float = 15.0,
        enabled: bool = True,
        terminal_retention_seconds: float = 86400.0,
    ):
        self.owner = owner
        self.enabled = enabled
        self.foreground_wait_seconds = max(0.01, foreground_wait_seconds)
        self.terminal_retention_seconds = max(0.0, terminal_retention_seconds)
        self._tasks: dict[str, asyncio.Task] = {}
        self._watchers: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._restored = False

    async def run(self, operation, on_complete, metadata: dict) -> JobRun:
        job_id = f"job-{int(time.time())}-{secrets.token_hex(3)}"

        async def on_handle(handle: GenerationHandle):
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "running",
                    "handle": handle.to_dict(),
                    "updated_at": time.time(),
                },
            )

        task = asyncio.create_task(operation(on_handle), name=job_id)
        self._tasks[job_id] = task
        if not self.enabled:
            try:
                return JobRun(job_id, False, await task)
            finally:
                await self._delete_job(job_id)
                self._tasks.pop(job_id, None)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=self.foreground_wait_seconds
            )
            await self._delete_job(job_id)
            self._tasks.pop(job_id, None)
            return JobRun(job_id, False, result)
        except TimeoutError:
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "running",
                    "updated_at": time.time(),
                },
            )
            self._spawn_watcher(job_id, task, on_complete, metadata)
            return JobRun(job_id, True)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._delete_job(job_id)
            self._tasks.pop(job_id, None)
            raise
        except Exception:
            await self._delete_job(job_id)
            self._tasks.pop(job_id, None)
            raise

    async def restore(self, resume_operation, on_complete, *, resume_enabled=True):
        async with self._lock:
            if self._restored:
                return
            self._restored = True
        await self._cleanup_terminal_jobs()
        for job_id in await self._job_ids():
            state = await self._get_job(job_id)
            if not isinstance(state, dict) or state.get("status") != "running":
                continue
            if not resume_enabled:
                await self._mark_unrecoverable(
                    job_id, state, "remote task restore disabled"
                )
                continue
            handle_data = state.get("handle")
            if not isinstance(handle_data, dict):
                await self._mark_unrecoverable(job_id, state, "missing remote handle")
                continue
            try:
                handle = GenerationHandle.from_dict(handle_data)
            except Exception as exc:
                await self._mark_unrecoverable(
                    job_id, state, f"invalid remote handle: {type(exc).__name__}"
                )
                continue
            task = asyncio.create_task(
                resume_operation(handle), name=f"resume-{job_id}"
            )
            self._tasks[job_id] = task
            self._spawn_watcher(job_id, task, on_complete, state, restored=True)

    async def terminate(self):
        tasks = [
            task for task in {*self._tasks.values(), *self._watchers} if not task.done()
        ]
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=3)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn_watcher(
        self, job_id, task, on_complete, metadata, *, restored: bool = False
    ):
        prefix = "watch-resume" if restored else "watch"
        watcher = asyncio.create_task(
            self._watch(job_id, task, on_complete, metadata),
            name=f"{prefix}-{job_id}",
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)

    async def _mark_unrecoverable(self, job_id: str, state: dict, reason: str):
        await self._save_job(
            job_id,
            {
                **state,
                "schema_version": 1,
                "status": "failed",
                "error": f"UnrecoverableJob: {reason}",
                "updated_at": time.time(),
            },
        )

    async def _watch(self, job_id, task, on_complete, metadata):
        try:
            result = await task
        except Exception as exc:
            try:
                completed = on_complete(job_id, None, exc, metadata)
                if inspect.isawaitable(completed):
                    await completed
            except Exception:
                logger.exception("Job completion callback failed for %s", job_id)
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "updated_at": time.time(),
                },
            )
            self._tasks.pop(job_id, None)
            return

        try:
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "delivering",
                    "updated_at": time.time(),
                },
            )
            completed = on_complete(job_id, result, None, metadata)
            if inspect.isawaitable(completed):
                completed = await completed
            terminal_status = "delivered"
            terminal_values = {}
            if isinstance(completed, dict):
                requested_status = str(completed.get("status") or "")
                if requested_status in {"delivered", "delivered_with_errors"}:
                    terminal_status = requested_status
                terminal_values = {
                    key: completed[key]
                    for key in ("delivery_errors", "delivered_count")
                    if key in completed
                }
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": terminal_status,
                    **terminal_values,
                    "updated_at": time.time(),
                },
            )
        except Exception as exc:
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "updated_at": time.time(),
                },
            )
        finally:
            self._tasks.pop(job_id, None)

    async def _save_job(self, job_id: str, value: dict):
        key = f"imagegen_jobs:{job_id}"
        current = await self._get(key, {})
        merged = {**current, **value} if isinstance(current, dict) else value
        await self._put(key, merged)
        ids = await self._job_ids()
        if job_id not in ids:
            ids.append(job_id)
            ids = await self._trim_job_index(ids, keep=200, current_job=job_id)
            await self._put(self.INDEX_KEY, ids)

    async def _trim_job_index(
        self, ids: list[str], *, keep: int, current_job: str
    ) -> list[str]:
        ids = list(ids)
        terminal = {"delivered", "delivered_with_errors", "failed"}
        for candidate in list(ids):
            if len(ids) <= keep:
                break
            if candidate == current_job:
                continue
            state = await self._get_job(candidate)
            if not isinstance(state, dict) or state.get("status") in terminal:
                ids.remove(candidate)
                await self._delete(f"imagegen_jobs:{candidate}")
        return ids

    async def _cleanup_terminal_jobs(self):
        cutoff = time.time() - self.terminal_retention_seconds
        for job_id in list(await self._job_ids()):
            state = await self._get_job(job_id)
            if not isinstance(state, dict):
                continue
            if state.get("status") not in {
                "delivered",
                "delivered_with_errors",
                "failed",
            }:
                continue
            try:
                updated_at = float(state.get("updated_at", 0))
            except (TypeError, ValueError):
                updated_at = 0.0
            if updated_at <= cutoff:
                await self._delete_job(job_id)

    async def _delete_job(self, job_id: str):
        await self._delete(f"imagegen_jobs:{job_id}")
        ids = [item for item in await self._job_ids() if item != job_id]
        await self._put(self.INDEX_KEY, ids)

    async def _get_job(self, job_id: str):
        return await self._get(f"imagegen_jobs:{job_id}", None)

    async def _job_ids(self) -> list[str]:
        value = await self._get(self.INDEX_KEY, [])
        return [str(item) for item in value] if isinstance(value, list) else []

    async def _get(self, key, default):
        method = getattr(self.owner, "get_kv_data", None)
        if not callable(method):
            return default
        try:
            return await method(key, default)
        except Exception:
            logger.warning("Failed to read plugin KV key %s", key, exc_info=True)
            return default

    async def _put(self, key, value):
        method = getattr(self.owner, "put_kv_data", None)
        if callable(method):
            try:
                await method(key, value)
            except Exception:
                logger.warning("Failed to write plugin KV key %s", key, exc_info=True)

    async def _delete(self, key):
        method = getattr(self.owner, "delete_kv_data", None)
        if callable(method):
            try:
                await method(key)
            except Exception:
                logger.warning("Failed to delete plugin KV key %s", key, exc_info=True)
