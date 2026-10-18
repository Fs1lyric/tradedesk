"""The shared pieces the API and the workers both need to reach brokers: settings, the
sessionmaker, the event bus, per-account locks and the broker factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.events import EventBus, RedisEventBus, order_channel, order_event
from app.core.locks import AccountLocks, RedisAccountLocks
from app.db import make_engine, make_sessionmaker
from app.models import BrokerAccount
from app.services.brokers.base import BrokerAdapter
from app.services.brokers.registry import BrokerFactory, build_broker
from app.services.execution.service import ExecutionService, OrderRecord
from app.services.execution.sql_store import SqlExecutionStore
from app.services.risk.manager import RiskLimits, RiskManager


@dataclass
class Runtime:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    bus: EventBus
    locks: AccountLocks
    broker_factory: BrokerFactory

    @classmethod
    def from_app_state(cls, state: Any) -> Runtime:
        return cls(state.settings, state.sessions, state.events, state.locks,
                   state.broker_factory)

    @classmethod
    def from_settings(cls, settings: Settings) -> Runtime:
        return cls(
            settings, make_sessionmaker(make_engine(settings.database_url)),
            RedisEventBus(settings.redis_url), RedisAccountLocks(settings.redis_url),
            build_broker,
        )

    async def aclose(self) -> None:
        await self.bus.aclose()
        await self.locks.aclose()
        engine = self.sessions.kw.get("bind")
        if engine is not None:
            await engine.dispose()

    async def publish_order(self, rec: OrderRecord) -> None:
        await self.bus.publish(order_channel(rec.account_id), order_event(rec))

    @asynccontextmanager
    async def broker(self, acct: BrokerAccount) -> AsyncIterator[BrokerAdapter]:
        broker = self.broker_factory(acct, self.settings)
        try:
            yield broker
        finally:
            aclose = getattr(broker, "aclose", None)
            if aclose is not None:
                await aclose()

    @asynccontextmanager
    async def execution(
        self, acct: BrokerAccount, lock: bool = True
    ) -> AsyncIterator[ExecutionService]:
        """An ExecutionService for one account that publishes every order change. With lock
        (the default) it holds the account lock, so executions for one account run one at a
        time; the trade stream passes lock=False because it only applies broker updates."""
        async with self.broker(acct) as broker:
            svc = ExecutionService(
                broker, RiskManager(RiskLimits.from_dict(acct.risk_limits)),
                SqlExecutionStore(self.sessions), on_order=self.publish_order,
            )
            if not lock:
                yield svc
                return
            async with self.locks.hold(acct.id):
                yield svc
