import asyncio
import os
import uuid

import pytest

from app.core.locks import LocalAccountLocks, LockTimeout, RedisAccountLocks

REDIS_URL = os.environ.get("REDIS_URL")


async def _serializes(locks) -> None:
    acct = str(uuid.uuid4())
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.hold(acct):
            order.append(f"{name}+")
            await asyncio.sleep(0.05)
            order.append(f"{name}-")

    await asyncio.gather(worker("a"), worker("b"))
    assert order in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"])
    async with locks.hold(acct), locks.hold(str(uuid.uuid4())):
        pass  # other accounts don't contend


async def test_local_locks():
    await _serializes(LocalAccountLocks())


@pytest.mark.skipif(REDIS_URL is None, reason="REDIS_URL not set")
async def test_redis_locks():
    locks = RedisAccountLocks(REDIS_URL, wait_s=2)
    try:
        await _serializes(locks)
        acct = str(uuid.uuid4())
        short = RedisAccountLocks(REDIS_URL, wait_s=0.1)
        async with locks.hold(acct):
            with pytest.raises(LockTimeout):
                async with short.hold(acct):
                    pass
        await short.aclose()
    finally:
        await locks.aclose()
