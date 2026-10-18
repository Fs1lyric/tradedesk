"""In-memory broker for tests and offline development. Marketable limit orders fill at once."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

from app.services.brokers.base import (
    Account,
    BrokerOrder,
    OrderRequest,
    Position,
    TradeUpdate,
)
from app.services.risk.manager import Quote


class FakeBroker:
    def __init__(self, cash: float = 100_000.0, now: datetime | None = None):
        self.cash = cash
        self.last_equity = cash
        self.quotes: dict[str, Quote] = {}
        self.positions: dict[str, tuple[float, float]] = {}  # symbol -> (qty, avg)
        self.orders: dict[str, BrokerOrder] = {}
        self.market_open = True
        self.tradable: set[str] = set()
        self.shortable: set[str] = set()
        self.reject_next = False
        self.fill_immediately = True
        self.updates: asyncio.Queue[TradeUpdate] = asyncio.Queue()
        self._ids = itertools.count(1)
        self._now = now

    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    def set_quote(self, symbol: str, bid: float, ask: float) -> None:
        self.quotes[symbol] = Quote(bid, ask, self.now())
        self.tradable.add(symbol)
        self.shortable.add(symbol)

    def _equity(self) -> float:
        return self.cash + sum(q * self.quotes[s].mid for s, (q, _) in self.positions.items())

    async def get_account(self) -> Account:
        eq = self._equity()
        return Account(equity=eq, cash=self.cash, buying_power=max(0.0, self.cash),
                       last_equity=self.last_equity)

    async def get_positions(self) -> list[Position]:
        return [
            Position(s, q, avg, q * self.quotes[s].mid)
            for s, (q, avg) in self.positions.items()
            if q != 0
        ]

    async def get_quote(self, symbol: str) -> Quote:
        q = self.quotes[symbol]
        return replace(q, ts=self.now())

    async def submit_order(self, req: OrderRequest) -> BrokerOrder:
        oid = f"B{next(self._ids)}"
        coid = req.client_order_id or oid
        if self.reject_next:
            self.reject_next = False
            order = BrokerOrder(oid, coid, req.symbol, req.side, req.qty, "rejected")
            self.orders[oid] = order
            await self.updates.put(TradeUpdate("rejected", order, at=self.now()))
            return order
        order = BrokerOrder(oid, coid, req.symbol, req.side, req.qty, "new", updated_at=self.now())
        self.orders[oid] = order
        await self.updates.put(TradeUpdate("new", order, at=self.now()))
        q = self.quotes[req.symbol]
        px = q.ask if req.side == "buy" else q.bid
        marketable = req.order_type == "market" or (
            req.limit_price is not None
            and (px <= req.limit_price if req.side == "buy" else px >= req.limit_price)
        )
        if self.fill_immediately and marketable:
            order = await self.fill(oid, req.qty, px)
        return order

    async def fill(self, oid: str, qty: float, price: float) -> BrokerOrder:
        o = self.orders[oid]
        signed = qty if o.side == "buy" else -qty
        held, avg = self.positions.get(o.symbol, (0.0, 0.0))
        new = held + signed
        if held == 0 or (held > 0) == (signed > 0):
            avg = (abs(held) * avg + qty * price) / abs(new) if new else 0.0
        elif new != 0 and (new > 0) != (held > 0):
            avg = price
        self.positions[o.symbol] = (new, avg)
        self.cash -= signed * price
        total = o.filled_qty + qty
        prev_px = o.filled_avg_price or 0.0
        avg_fill = (o.filled_qty * prev_px + qty * price) / total
        status = "filled" if total >= o.qty else "partially_filled"
        o = replace(o, status=status, filled_qty=total, filled_avg_price=avg_fill,
                    updated_at=self.now())
        self.orders[oid] = o
        await self.updates.put(
            TradeUpdate("fill" if status == "filled" else "partial_fill", o,
                        fill_id=f"F{next(self._ids)}", fill_qty=qty, fill_price=price,
                        at=self.now())
        )
        return o

    async def cancel_order(self, broker_order_id: str) -> None:
        o = replace(self.orders[broker_order_id], status="canceled", updated_at=self.now())
        self.orders[broker_order_id] = o
        await self.updates.put(TradeUpdate("canceled", o, at=self.now()))

    async def cancel_all(self) -> None:
        for oid, o in list(self.orders.items()):
            if o.status in ("new", "partially_filled", "accepted"):
                await self.cancel_order(oid)

    async def close_all_positions(self) -> None:
        for sym, (qty, _) in list(self.positions.items()):
            if qty:
                side = "sell" if qty > 0 else "buy"
                await self.submit_order(OrderRequest(sym, side, abs(qty), order_type="market"))

    async def list_orders(self, since: datetime) -> list[BrokerOrder]:
        return list(self.orders.values())

    async def is_market_open(self) -> bool:
        return self.market_open

    async def asset_flags(self, symbol: str) -> tuple[bool, bool]:
        return symbol in self.tradable, symbol in self.shortable

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]:
        while True:
            yield await self.updates.get()
