from __future__ import annotations

import asyncio
import os
import time


class CleanupManager:
    def __init__(
        self,
        root: str,
        *,
        enabled: bool = True,
        ttl_seconds: int = 86400,
        interval_seconds: int = 3600,
    ):
        self.root = os.path.realpath(root)
        self.enabled = enabled
        self.ttl_seconds = max(60, int(ttl_seconds or 86400))
        self.interval_seconds = max(60, int(interval_seconds or 3600))
        self._task: asyncio.Task | None = None

    def start(self, active_paths=None):
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(active_paths or ()), name="imagegen-cleanup"
        )

    async def stop(self):
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_once(self, active_paths=()) -> list[str]:
        return await asyncio.to_thread(self._run_once_sync, set(active_paths))

    async def _loop(self, active_paths):
        while True:
            current_paths = active_paths() if callable(active_paths) else active_paths
            await self.run_once(current_paths)
            await asyncio.sleep(self.interval_seconds)

    def _run_once_sync(self, active_paths: set[str]) -> list[str]:
        if not os.path.isdir(self.root):
            return []
        active = {os.path.realpath(path) for path in active_paths if path}
        cutoff = time.time() - self.ttl_seconds
        removed = []
        for current_root, _dirs, files in os.walk(self.root):
            for filename in files:
                path = os.path.realpath(os.path.join(current_root, filename))
                if not _within(path, self.root) or path in active:
                    continue
                try:
                    if os.path.getmtime(path) >= cutoff:
                        continue
                    os.remove(path)
                    removed.append(path)
                except OSError:
                    continue
        return removed


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False
