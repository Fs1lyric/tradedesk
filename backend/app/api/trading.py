"""Shared plumbing for endpoints that reach a broker: the adapter factory, an
ExecutionService per account, and the order schema."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Request
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.core.security import decrypt_secret
from app.models import BrokerAccount
from app.services.brokers.alpaca import AlpacaAdapter
from app.services.brokers.base import BrokerAdapter
from app.services.execution.service import ExecutionService
from app.services.execution.sql_store import SqlExecutionStore
from app.services.risk.manager import RiskLimits, RiskManager

BrokerFactory = Callable[[BrokerAccount, Settings], BrokerAdapter]


def alpaca_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    key = settings.master_key_bytes
    return AlpacaAdapter(
        decrypt_secret(acct.api_key_enc, key), decrypt_secret(acct.api_secret_enc, key),
        paper=acct.mode == "paper",
    )


@asynccontextmanager
async def broker_for(request: Request, acct: BrokerAccount) -> AsyncIterator[BrokerAdapter]:
    factory: BrokerFactory = request.app.state.broker_factory
    broker = factory(acct, request.app.state.settings)
    try:
        yield broker
    finally:
        aclose = getattr(broker, "aclose", None)
        if aclose is not None:
            await aclose()


@asynccontextmanager
async def execution_service(
    request: Request, acct: BrokerAccount
) -> AsyncIterator[ExecutionService]:
    """The store writes in its own transactions, so orders and intents are recorded even if
    the request that placed them fails afterwards."""
    async with broker_for(request, acct) as broker:
        yield ExecutionService(
            broker, RiskManager(RiskLimits.from_dict(acct.risk_limits)),
            SqlExecutionStore(request.app.state.sessions),
        )


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    qty: float
    order_type: str
    order_class: str
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    time_in_force: str
    status: str
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None
    updated_at: datetime | None
