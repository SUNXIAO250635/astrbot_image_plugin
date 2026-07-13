from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .models import GenerationHandle


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
    ):
        self.owner = owner
        self.enabled = enabled
        self.foreground_wait_seconds = max(0.01, foreground_wait_seconds)
        self._tasks: dict[str, asyncio.Task] = {}
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
            asyncio.create_task(
                self._watch(job_id, task, on_complete, metadata),
                name=f"watch-{job_id}",
            )
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

    async def restore(self, resume_operation, on_complete):
        async with self._lock:
            if self._restored:
                return
            self._restored = True
        for job_id in await self._job_ids():
            state = await self._get_job(job_id)
            if not isinstance(state, dict) or state.get("status") != "running":
                continue
            handle_data = state.get("handle")
            if not isinstance(handle_data, dict):
                continue
            try:
                handle = GenerationHandle.from_dict(handle_data)
            except Exception:
                continue
            task = asyncio.create_task(resume_operation(handle), name=f"resume-{job_id}")
            self._tasks[job_id] = task
            asyncio.create_task(
                self._watch(job_id, task, on_complete, state),
                name=f"watch-resume-{job_id}",
            )

    async def terminate(self):
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=3)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _watch(self, job_id, task, on_complete, metadata):
        try:
            result = await task
            await self._save_job(
                job_id,
                {**metadata, "schema_version": 1, "status": "delivering"},
            )
            completed = on_complete(job_id, result, None, metadata)
            if inspect.isawaitable(completed):
                await completed
            await self._save_job(
                job_id,
                {**metadata, "schema_version": 1, "status": "delivered"},
            )
        except Exception as exc:
            completed = on_complete(job_id, None, exc, metadata)
            if inspect.isawaitable(completed):
                await completed
            await self._save_job(
                job_id,
                {
                    **metadata,
                    "schema_version": 1,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
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
            await self._put(self.INDEX_KEY, ids[-200:])

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
            return default

    async def _put(self, key, value):
        method = getattr(self.owner, "put_kv_data", None)
        if callable(method):
            try:
                await method(key, value)
            except Exception:
                pass

    async def _delete(self, key):
        method = getattr(self.owner, "delete_kv_data", None)
        if callable(method):
            try:
                await method(key)
            except Exception:
                pass
