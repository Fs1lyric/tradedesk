"""Per-account locks so two executions for one account (API, rule worker) can't interleave
their risk checks and both pass on the same stale state."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import redis.asyncio as aioredis
from redis.exceptions import LockError


class LockTimeout(Exception):
    pass


class AccountLocks(Protocol):
    def hold(self, account_id: str) -> AbstractAsyncContextManager[None]: ...

    async def aclose(self) -> None: ...


class LocalAccountLocks:
    """In-process locks, for tests and single-process use."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def hold(self, account_id: str) -> AsyncIterator[None]:
        async with self._locks.setdefault(account_id, asyncio.Lock()):
            yield

    async def aclose(self) -> None:
        pass


class RedisAccountLocks:
    def __init__(self, url: str, ttl_s: float = 30.0, wait_s: float = 10.0) -> None:
        self.redis = aioredis.from_url(url)
        self.ttl_s = ttl_s  # a crashed holder frees the lock after this long
        self.wait_s = wait_s

    @asynccontextmanager
    async def hold(self, account_id: str) -> AsyncIterator[None]:
        lock = self.redis.lock(f"lock:account:{account_id}", timeout=self.ttl_s,
                               blocking_timeout=self.wait_s)
        if not await lock.acquire():
            raise LockTimeout(account_id)
        try:
            yield
        finally:
            with contextlib.suppress(LockError):  # expired under us; nothing to release
                await lock.release()

    async def aclose(self) -> None:
        await self.redis.aclose()
