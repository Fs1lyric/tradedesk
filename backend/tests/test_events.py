import asyncio
import os
import uuid

import pytest

from app.core.events import InMemoryEventBus, RedisEventBus

REDIS_URL = os.environ.get("REDIS_URL")


async def _roundtrip(bus) -> None:
    a, b = f"orders:{uuid.uuid4()}", f"orders:{uuid.uuid4()}"
    async with bus.subscribe([a]) as stream:
        await bus.publish(b, {"n": 0})  # not subscribed
        await bus.publish(a, {"n": 1})
        msg = await asyncio.wait_for(anext(stream), 5)
    assert msg == {"n": 1}


async def test_in_memory_bus():
    await _roundtrip(InMemoryEventBus())


@pytest.mark.skipif(REDIS_URL is None, reason="REDIS_URL not set")
async def test_redis_bus():
    bus = RedisEventBus(REDIS_URL)
    try:
        await _roundtrip(bus)
    finally:
        await bus.aclose()
