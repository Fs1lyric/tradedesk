"""Shared plumbing for endpoints that reach a broker, and the order schema."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.core.locks import LockTimeout
from app.models import BrokerAccount
from app.runtime import Runtime
from app.services.brokers.base import BrokerAdapter
from app.services.execution.service import ExecutionService


def runtime(request: Request) -> Runtime:
    return Runtime.from_app_state(request.app.state)


def broker_for(request: Request, acct: BrokerAccount
               ) -> AbstractAsyncContextManager[BrokerAdapter]:
    return runtime(request).broker(acct)


@asynccontextmanager
async def execution_service(
    request: Request, acct: BrokerAccount
) -> AsyncIterator[ExecutionService]:
    """The store writes in its own transactions, so orders and intents are recorded even if
    the request that placed them fails afterwards."""
    try:
        async with runtime(request).execution(acct) as svc:
            yield svc
    except LockTimeout as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "account is busy; retry") from e


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
