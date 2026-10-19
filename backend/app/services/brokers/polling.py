"""Trade updates for brokers without a push stream: poll open orders and diff them."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.brokers.base import BrokerOrder, TradeUpdate

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)

OrderState = dict[str, BrokerOrder]


def us_market_open(now: datetime) -> bool:
    """Regular session hours in New York. Exchange holidays are not modelled."""
    et = now.astimezone(ET)
    return et.weekday() < 5 and OPEN <= et.time() < CLOSE


def _fill(prev: BrokerOrder | None, cur: BrokerOrder) -> TradeUpdate | None:
    """The fill since `prev`, priced from the change in cumulative notional."""
    before = prev.filled_qty if prev else 0.0
    qty = cur.filled_qty - before
    if qty <= 0 or cur.filled_avg_price is None:
        return None
    notional = cur.filled_qty * cur.filled_avg_price
    if prev and prev.filled_avg_price is not None:
        notional -= before * prev.filled_avg_price
    event = "fill" if cur.status == "filled" else "partial_fill"
    return TradeUpdate(event, cur, fill_id=f"{cur.broker_order_id}:{cur.filled_qty:g}",
                       fill_qty=qty, fill_price=notional / qty, at=cur.updated_at)


def diff_orders(
    state: OrderState, orders: list[BrokerOrder]
) -> tuple[list[TradeUpdate], OrderState]:
    """Updates implied by a fresh order list, and the state to diff the next list against."""
    ups: list[TradeUpdate] = []
    nxt = dict(state)
    for cur in orders:
        prev = state.get(cur.broker_order_id)
        nxt[cur.broker_order_id] = cur
        if prev is None:
            ups.append(TradeUpdate("new", cur, at=cur.updated_at))
        fill = _fill(prev, cur)
        if fill is not None:
            ups.append(fill)
        elif prev is not None and prev.status != cur.status:
            ups.append(TradeUpdate(cur.status, cur, at=cur.updated_at))
    return ups, nxt


async def poll_trade_updates(
    list_orders: Callable[[datetime], Awaitable[list[BrokerOrder]]],
    interval: float = 2.0,
    lookback: timedelta = timedelta(days=1),
) -> AsyncIterator[TradeUpdate]:
    """Yield updates forever. The first poll only seeds the state, so orders that existed
    before the stream started don't replay as new."""
    state: OrderState | None = None
    while True:
        try:
            orders = await list_orders(datetime.now(UTC) - lookback)
            if state is None:
                state = {o.broker_order_id: o for o in orders}
            else:
                ups, state = diff_orders(state, orders)
                for u in ups:
                    yield u
        except Exception:
            log.exception("order poll failed")
        await asyncio.sleep(interval)
