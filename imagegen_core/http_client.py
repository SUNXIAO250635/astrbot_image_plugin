from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

import aiohttp


_sessions: WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.ClientSession] = (
    WeakKeyDictionary()
)
_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


async def get_session() -> aiohttp.ClientSession:
    """Return the shared HTTP session owned by the current event loop."""
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is not None and not session.closed:
        return session

    lock = _locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _locks[loop] = lock

    async with lock:
        session = _sessions.get(loop)
        if session is None or session.closed:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
            _sessions[loop] = session
        return session


async def close_all_sessions() -> None:
    """Close and forget every session created by this process."""
    sessions = list(_sessions.values())
    _sessions.clear()
    _locks.clear()
    if sessions:
        await asyncio.gather(
            *(session.close() for session in sessions if not session.closed)
        )
