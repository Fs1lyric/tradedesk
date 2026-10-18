"""Manual orders, cancels, reconciliation, the kill switch, and trades from fills."""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, PositiveFloat, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, is_uuid, not_found, owned_account
from app.api.trading import OrderOut, broker_for, execution_service
from app.api.watchlists import normalize_symbol
from app.models import AuditLog, BrokerAccount, EquitySnapshot, FillRow, Order, User
from app.services.analytics.lots import Fill, Trade, match_fills
from app.services.analytics.metrics import drawdown_series, summarize
from app.services.execution.service import TradeIntent
from app.services.execution.state import TERMINAL

router = APIRouter(tags=["orders"])


class OrderIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell", "short", "cover"]
    qty: PositiveFloat
    order_type: Literal["market", "limit"] = "limit"
    limit_price: PositiveFloat | None = None  # limit orders without one price off the quote
    stop_loss: PositiveFloat | None = None
    take_profit: PositiveFloat | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    extended_hours: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _market_has_no_limit(self) -> OrderIn:
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("market orders take no limit_price")
        return self


class ExecutionOut(BaseModel):
    risk: dict[str, Any]
    order: OrderOut | None


class KillIn(BaseModel):
    flatten: bool = False


class TradeOut(BaseModel):
    symbol: str
    direction: str
    qty: float
    entry_at: datetime
    entry_price: float
    exit_qty: float
    exit_price: float | None
    exit_at: datetime | None
    fees: float
    realized_pnl: float
    return_pct: float
    closed: bool


@router.post("/accounts/{account_id}/orders", response_model=ExecutionOut)
async def place_order(
    account_id: str, body: OrderIn, request: Request, user: UserDep, session: SessionDep
) -> ExecutionOut:
    acct = await owned_account(session, user, account_id)
    key = body.idempotency_key or uuid.uuid4().hex
    intent = TradeIntent(
        key=f"manual:{acct.id}:{key}", account_id=acct.id,
        symbol=normalize_symbol(body.symbol), side=body.side, qty=body.qty,
        order_type=body.order_type, limit_price=body.limit_price, stop_loss=body.stop_loss,
        take_profit=body.take_profit, time_in_force=body.time_in_force,
        extended_hours=body.extended_hours, origin="manual",
    )
    async with execution_service(request, acct) as svc:
        result = await svc.execute(intent)
    return ExecutionOut(
        risk=result.risk.to_dict(),
        order=OrderOut.model_validate(result.order) if result.order else None,
    )


@router.get("/accounts/{account_id}/orders", response_model=list[OrderOut])
async def list_orders(
    account_id: str, user: UserDep, session: SessionDep,
    status: Literal["open", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Order]:
    acct = await owned_account(session, user, account_id)
    q = select(Order).where(Order.account_id == acct.id)
    if status == "open":
        q = q.where(Order.status.not_in([s.value for s in TERMINAL]))
    q = q.order_by(Order.submitted_at.desc()).limit(limit)
    return list(await session.scalars(q))


async def _owned_order(session: AsyncSession, user: User, order_id: str
                       ) -> tuple[Order, BrokerAccount]:
    row = None
    if is_uuid(order_id):
        row = (await session.execute(
            select(Order, BrokerAccount)
            .join(BrokerAccount, BrokerAccount.id == Order.account_id)
            .where(Order.id == order_id, BrokerAccount.user_id == user.id)
        )).first()
    if row is None:
        raise not_found("order not found")
    return row[0], row[1]


@router.post("/orders/{order_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_order(
    order_id: str, request: Request, user: UserDep, session: SessionDep
) -> dict[str, str]:
    """Ask the broker to cancel. The status changes when the broker confirms, through the
    trade stream or a reconcile."""
    order, acct = await _owned_order(session, user, order_id)
    if order.status in {s.value for s in TERMINAL} or order.broker_order_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"order is {order.status}")
    async with broker_for(request, acct) as broker:
        await broker.cancel_order(order.broker_order_id)
    return {"id": order.id, "status": "cancel_requested"}


@router.post("/accounts/{account_id}/reconcile")
async def reconcile(
    account_id: str, request: Request, user: UserDep, session: SessionDep
) -> dict[str, list[str]]:
    acct = await owned_account(session, user, account_id)
    async with execution_service(request, acct) as svc:
        return {"changed": await svc.reconcile(acct.id)}


@router.post("/accounts/{account_id}/kill")
async def kill(
    account_id: str, body: KillIn, request: Request, user: UserDep, session: SessionDep
) -> dict[str, bool]:
    """Block new orders, cancel open ones and, with flatten, close every position."""
    acct = await owned_account(session, user, account_id)
    async with execution_service(request, acct) as svc:
        await svc.set_kill_switch(acct.id, True, flatten=body.flatten)
    session.add(AuditLog(user_id=user.id, action="kill_switch",
                         detail={"account_id": acct.id, "on": True, "flatten": body.flatten}))
    return {"kill_switch": True, "flattened": body.flatten}


async def _trades(session: AsyncSession, account_id: str) -> list[Trade]:
    rows = await session.scalars(
        select(FillRow).where(FillRow.account_id == account_id)
        .order_by(FillRow.filled_at, FillRow.id)
    )
    return match_fills([Fill(f.broker_fill_id, f.symbol, f.qty, f.price, f.filled_at, f.fee)
                        for f in rows])


@router.get("/accounts/{account_id}/trades", response_model=list[TradeOut])
async def list_trades(
    account_id: str, user: UserDep, session: SessionDep,
    status: Literal["open", "closed", "all"] = "all",
) -> list[TradeOut]:
    acct = await owned_account(session, user, account_id)
    trades = sorted(await _trades(session, acct.id), key=lambda t: t.entry_at)
    if status != "all":
        trades = [t for t in trades if t.closed == (status == "closed")]
    return [TradeOut(
        symbol=t.symbol, direction=t.direction, qty=t.qty, entry_at=t.entry_at,
        entry_price=t.entry_price, exit_qty=t.exit_qty, exit_price=t.exit_price,
        exit_at=t.exit_at, fees=t.fees, realized_pnl=t.realized_pnl, return_pct=t.return_pct,
        closed=t.closed,
    ) for t in trades]


def _finite_or_none(v: float | int | None) -> float | int | None:
    return v if v is None or math.isfinite(v) else None


@router.get("/accounts/{account_id}/analytics")
async def analytics(account_id: str, user: UserDep, session: SessionDep) -> dict[str, Any]:
    acct = await owned_account(session, user, account_id)
    snaps = list(await session.scalars(
        select(EquitySnapshot).where(EquitySnapshot.account_id == acct.id)
        .order_by(EquitySnapshot.ts)
    ))
    equity = np.array([s.equity for s in snaps], dtype=float)
    pnls = np.array([t.realized_pnl for t in await _trades(session, acct.id) if t.closed])
    metrics = {k: _finite_or_none(v) for k, v in summarize(equity, trade_pnls=pnls).items()}
    dd = drawdown_series(equity) if len(equity) else np.array([])
    return {
        "metrics": metrics,
        "equity": [{"ts": s.ts, "equity": s.equity, "drawdown": float(d)}
                   for s, d in zip(snaps, dd, strict=True)],
    }
