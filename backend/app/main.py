"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import (
    accounts,
    auth,
    backtests,
    news,
    orders,
    recommendations,
    rules,
    watchlists,
    ws,
)
from app.core.config import Settings, get_settings
from app.core.events import EventBus, RedisEventBus
from app.core.locks import AccountLocks, RedisAccountLocks
from app.db import make_engine, make_sessionmaker
from app.services.brokers.registry import build_broker


def create_app(
    settings: Settings | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    events: EventBus | None = None,
    locks: AccountLocks | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, sessionmaker, event bus and locks; otherwise
    they come from the environment and the engine lives as long as the app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if sessions is None:
            engine = make_engine(app.state.settings.database_url)
            app.state.sessions = make_sessionmaker(engine)
        yield
        await app.state.events.aclose()
        await app.state.locks.aclose()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="tradedesk", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.sessions = sessions
    # Built here, not in lifespan, because httpx's ASGITransport never runs lifespan.
    app.state.events = events or RedisEventBus(app.state.settings.redis_url)
    app.state.locks = locks or RedisAccountLocks(app.state.settings.redis_url)
    app.state.broker_factory = build_broker
    for module in (
        auth, accounts, watchlists, rules, news, recommendations, orders, backtests, ws,
    ):
        app.include_router(module.router)
    return app
