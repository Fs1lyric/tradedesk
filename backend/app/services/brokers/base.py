"""Broker adapter protocol and the shared data types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.services.risk.manager import Quote


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    last_equity: float  # previous close, used as start-of-day equity
    pattern_day_trader: bool = False
    daytrade_count: int = 0


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float  # signed
    avg_entry_price: float
    market_value: float


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # buy | sell (shorts are sells with no position; adapters map short/cover)
    qty: float
    order_type: str = "limit"  # market | limit | stop | stop_limit
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    order_class: str = "simple"  # simple | bracket | oco
    take_profit: float | None = None
    stop_loss: float | None = None
    client_order_id: str | None = None
    extended_hours: bool = False


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str  # normalized: see execution.state.OrderStatus
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    legs: list[BrokerOrder] = field(default_factory=list)
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TradeUpdate:
    event: str  # new | fill | partial_fill | canceled | expired | rejected | ...
    order: BrokerOrder
    fill_id: str | None = None
    fill_qty: float | None = None
    fill_price: float | None = None
    at: datetime | None = None


class BrokerAdapter(Protocol):
    async def get_account(self) -> Account: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_quote(self, symbol: str) -> Quote: ...
    async def submit_order(self, req: OrderRequest) -> BrokerOrder: ...
    async def cancel_order(self, broker_order_id: str) -> None: ...
    async def cancel_all(self) -> None: ...
    async def close_all_positions(self) -> None: ...
    async def list_orders(self, since: datetime) -> list[BrokerOrder]: ...
    async def is_market_open(self) -> bool: ...
    async def asset_flags(self, symbol: str) -> tuple[bool, bool]: ...  # tradable, shortable
    def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]: ...
