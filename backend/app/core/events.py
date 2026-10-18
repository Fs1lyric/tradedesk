"""Pub/sub for live events (order updates) between workers and WebSocket clients.

`subscribe` is a context manager so the subscription is live before the caller reports
itself ready; an async generator would only subscribe on its first iteration and could miss
messages published in between.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import redis.asyncio as aioredis

Event = dict[str, Any]


class EventBus(Protocol):
    async def publish(self, channel: str, message: Event) -> None: ...

    def subscribe(self, channels: list[str]) -> Any:
        """An async context manager yielding an async iterator of events."""
        ...

    async def aclose(self) -> None: ...


class InMemoryEventBus:
    """Single-process bus for tests."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[Event]]] = {}

    async def publish(self, channel: str, message: Event) -> None:
        for q in self._queues.get(channel, ()):
            q.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self, channels: list[str]) -> AsyncIterator[AsyncIterator[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        for ch in channels:
            self._queues.setdefault(ch, set()).add(q)

        async def stream() -> AsyncIterator[Event]:
            while True:
                yield await q.get()

        try:
            yield stream()
        finally:
            for ch in channels:
                self._queues[ch].discard(q)

    async def aclose(self) -> None:
        pass


class RedisEventBus:
    def __init__(self, url: str) -> None:
        # Lazy: no connection is made until the first publish or subscribe.
        self.redis = aioredis.from_url(url, decode_responses=True)

    async def publish(self, channel: str, message: Event) -> None:
        await self.redis.publish(channel, json.dumps(message, default=str))

    @asynccontextmanager
    async def subscribe(self, channels: list[str]) -> AsyncIterator[AsyncIterator[Event]]:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*channels)

        async def stream() -> AsyncIterator[Event]:
            async for m in pubsub.listen():
                if m["type"] == "message":
                    yield json.loads(m["data"])

        try:
            yield stream()
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def aclose(self) -> None:
        await self.redis.aclose()


def order_channel(account_id: str) -> str:
    return f"orders:{account_id}"


def order_event(rec: Any) -> Event:
    """The WebSocket message for an order (an OrderRecord or an orders row)."""
    fields = ("id", "account_id", "client_order_id", "broker_order_id", "symbol", "side", "qty",
              "order_type", "order_class", "limit_price", "stop_loss", "take_profit",
              "time_in_force", "status", "filled_qty", "filled_avg_price", "submitted_at",
              "updated_at")
    order = {f: getattr(rec, f) for f in fields}
    order["status"] = str(order["status"])
    for f in ("submitted_at", "updated_at"):
        if order[f] is not None:
            order[f] = order[f].isoformat()
    return {"type": "order", "order": order}
