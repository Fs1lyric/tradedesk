"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import accounts, auth, news, orders, recommendations, rules, watchlists
from app.api.trading import alpaca_broker
from app.core.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker


def create_app(
    settings: Settings | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings and sessionmaker; otherwise both come
    from the environment and the engine lives as long as the app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if sessions is not None:
            yield
            return
        engine = make_engine(app.state.settings.database_url)
        app.state.sessions = make_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="tradedesk", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.sessions = sessions
    app.state.broker_factory = alpaca_broker
    for module in (
        auth, accounts, watchlists, rules, news, recommendations, orders,
    ):
        app.include_router(module.router)
    return app
