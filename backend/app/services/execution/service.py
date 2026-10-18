"""Execution service: intent -> risk -> order -> broker, plus trade updates and reconciliation.

Run one ExecutionService call at a time per account (the Celery task holds a Redis lock), so
sizing and risk checks never race each other.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from app.services.analytics.lots import Fill, match_fills
from app.services.brokers.base import BrokerAdapter, BrokerOrder, OrderRequest, TradeUpdate
from app.services.execution.state import (
    InvalidTransition,
    OrderStatus,
    normalize_broker_status,
    transition,
)
from app.services.risk.manager import (
    AccountState,
    OrderIntent,
    RiskManager,
    RiskResult,
    cooldown_until,
)

log = logging.getLogger(__name__)
S = OrderStatus
BUY_SIDES = {"buy", "cover"}
DEFAULT_SLIPPAGE_BPS = 10.0


def client_order_id(intent_key: str) -> str:
    return hashlib.sha256(intent_key.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class TradeIntent:
    key: str
    account_id: str
    symbol: str
    side: str  # buy | sell | short | cover
    qty: float
    order_type: str = "limit"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    time_in_force: str = "day"
    extended_hours: bool = False
    origin: str = "manual"
    rule_id: str | None = None
    recommendation_id: str | None = None
    cooldown_minutes: int | None = None


@dataclass
class OrderRecord:
    id: str
    intent_key: str
    account_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    order_class: str
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    time_in_force: str
    status: OrderStatus = S.NEW
    broker_order_id: str | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FillRecord:
    order_id: str
    account_id: str
    broker_fill_id: str
    symbol: str
    qty: float  # signed
    price: float
    filled_at: datetime
    fee: float = 0.0


@dataclass
class IntentRecord:
    intent: TradeIntent
    risk: dict[str, Any]
    created_at: datetime


class ExecutionStore(Protocol):
    async def save_intent(self, rec: IntentRecord) -> None: ...
    async def add_order(self, rec: OrderRecord) -> None: ...
    async def update_order(self, rec: OrderRecord) -> None: ...
    async def find_order(
        self, broker_order_id: str | None, client_order_id: str | None
    ) -> OrderRecord | None: ...
    async def open_orders(self, account_id: str) -> list[OrderRecord]: ...
    async def add_fill(self, rec: FillRecord) -> bool: ...
    async def fills(self, account_id: str, since: datetime) -> list[FillRecord]: ...
    async def recent_intent_keys(self, account_id: str) -> set[str]: ...
    async def order_times(self, account_id: str, since: datetime) -> list[datetime]: ...
    async def cooldowns(self, account_id: str) -> dict[tuple[str, str], datetime]: ...
    async def set_cooldown(self, account_id: str, rule_id: str, symbol: str, until: datetime) -> None: ...  # noqa: E501
    async def is_halted(self, account_id: str, day: datetime) -> bool: ...
    async def set_halted(self, account_id: str, day: datetime) -> None: ...
    async def kill_switch(self, account_id: str) -> bool: ...
    async def set_kill_switch(self, account_id: str, on: bool) -> None: ...


@dataclass
class InMemoryExecutionStore:
    intents: dict[str, IntentRecord] = field(default_factory=dict)
    orders: dict[str, OrderRecord] = field(default_factory=dict)
    fill_list: list[FillRecord] = field(default_factory=list)
    halted: dict[str, set[date]] = field(default_factory=dict)
    kill: dict[str, bool] = field(default_factory=dict)
    cooldown_map: dict[str, dict[tuple[str, str], datetime]] = field(default_factory=dict)

    async def save_intent(self, rec: IntentRecord) -> None:
        self.intents[rec.intent.key] = rec

    async def add_order(self, rec: OrderRecord) -> None:
        self.orders[rec.id] = rec

    async def update_order(self, rec: OrderRecord) -> None:
        self.orders[rec.id] = rec

    async def find_order(
        self, broker_order_id: str | None, client_order_id: str | None
    ) -> OrderRecord | None:
        for o in self.orders.values():
            if broker_order_id and o.broker_order_id == broker_order_id:
                return o
            if client_order_id and o.client_order_id == client_order_id:
                return o
        return None

    async def open_orders(self, account_id: str) -> list[OrderRecord]:
        return [o for o in self.orders.values()
                if o.account_id == account_id and not o.status.terminal]

    async def add_fill(self, rec: FillRecord) -> bool:
        if any(f.broker_fill_id == rec.broker_fill_id for f in self.fill_list):
            return False
        self.fill_list.append(rec)
        return True

    async def fills(self, account_id: str, since: datetime) -> list[FillRecord]:
        return [f for f in self.fill_list if f.account_id == account_id and f.filled_at >= since]

    async def recent_intent_keys(self, account_id: str) -> set[str]:
        return {k for k, r in self.intents.items() if r.intent.account_id == account_id}

    async def order_times(self, account_id: str, since: datetime) -> list[datetime]:
        return [o.submitted_at for o in self.orders.values()
                if o.account_id == account_id and o.submitted_at and o.submitted_at >= since]

    async def cooldowns(self, account_id: str) -> dict[tuple[str, str], datetime]:
        return dict(self.cooldown_map.get(account_id, {}))

    async def set_cooldown(self, account_id: str, rule_id: str, symbol: str, until: datetime) -> None:  # noqa: E501
        self.cooldown_map.setdefault(account_id, {})[(rule_id, symbol)] = until

    async def is_halted(self, account_id: str, day: datetime) -> bool:
        return day.date() in self.halted.get(account_id, set())

    async def set_halted(self, account_id: str, day: datetime) -> None:
        self.halted.setdefault(account_id, set()).add(day.date())

    async def kill_switch(self, account_id: str) -> bool:
        return self.kill.get(account_id, False)

    async def set_kill_switch(self, account_id: str, on: bool) -> None:
        self.kill[account_id] = on


@dataclass
class ExecutionResult:
    risk: RiskResult
    order: OrderRecord | None


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class ExecutionService:
    def __init__(
        self,
        broker: BrokerAdapter,
        risk: RiskManager,
        store: ExecutionStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
        on_order: Callable[[OrderRecord], Awaitable[None]] | None = None,
    ):
        self.broker = broker
        self.risk = risk
        self.store = store
        self.clock = clock
        self.slippage_bps = slippage_bps
        self.on_order = on_order  # called after every stored order change, e.g. to publish

    async def _save(self, rec: OrderRecord) -> None:
        await self.store.update_order(rec)
        await self._notify(rec)

    async def _notify(self, rec: OrderRecord) -> None:
        if self.on_order is not None:
            try:
                await self.on_order(rec)
            except Exception:
                log.exception("on_order hook failed for %s", rec.id)

    async def _account_state(self, account_id: str, now: datetime, symbol: str) -> AccountState:
        acct = await self.broker.get_account()
        positions = {p.symbol: p.market_value for p in await self.broker.get_positions()}
        tradable, shortable = await self.broker.asset_flags(symbol)
        times = await self.store.order_times(account_id, _day_start(now))
        return AccountState(
            equity=acct.equity,
            buying_power=acct.buying_power,
            start_of_day_equity=acct.last_equity,
            positions=positions,
            market_open=await self.broker.is_market_open(),
            kill_switch=await self.store.kill_switch(account_id),
            halted=await self.store.is_halted(account_id, now),
            orders_last_minute=sum(1 for t in times if t >= now - timedelta(minutes=1)),
            orders_today=len(times),
            day_trades_5d=await self._day_trades(account_id, now),
            recent_intent_keys=await self.store.recent_intent_keys(account_id),
            cooldowns=await self.store.cooldowns(account_id),
            tradable={symbol} if tradable else set(),
            shortable={symbol} if shortable else set(),
        )

    async def _day_trades(self, account_id: str, now: datetime) -> int:
        since = _day_start(now) - timedelta(days=7)
        fills = await self.store.fills(account_id, since)
        trades = match_fills(
            [Fill(f.broker_fill_id, f.symbol, f.qty, f.price, f.filled_at, f.fee) for f in fills]
        )
        return sum(
            1 for t in trades
            if t.exit_at is not None and t.exit_at.date() == t.entry_at.date()
        )

    async def _opened_today(self, account_id: str, symbol: str, now: datetime) -> bool:
        return any(f.symbol == symbol for f in await self.store.fills(account_id, _day_start(now)))

    def _limit_price(self, intent: TradeIntent, bid: float, ask: float) -> float | None:
        if intent.order_type == "market":
            return None
        if intent.limit_price is not None:
            return intent.limit_price
        slip = self.slippage_bps / 1e4
        if intent.side in BUY_SIDES:
            return round(ask * (1 + slip), 4)
        return round(bid * (1 - slip), 4)

    async def execute(self, intent: TradeIntent) -> ExecutionResult:
        now = self.clock()
        quote = await self.broker.get_quote(intent.symbol)
        state = await self._account_state(intent.account_id, now, intent.symbol)
        held = state.positions.get(intent.symbol, 0.0)
        reducing = held != 0 and ((held > 0) != (intent.side in BUY_SIDES))
        limit = self._limit_price(intent, quote.bid, quote.ask)
        risk_intent = OrderIntent(
            key=intent.key,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            limit_price=limit,
            quote=quote,
            rule_id=intent.rule_id,
            would_be_day_trade=reducing
            and await self._opened_today(intent.account_id, intent.symbol, now),
            extended_hours=intent.extended_hours,
        )
        result = self.risk.check(risk_intent, state, now)
        await self.store.save_intent(IntentRecord(intent, result.to_dict(), now))
        if result.halt_account:
            await self.store.set_halted(intent.account_id, now)
        if not result.passed:
            log.info("intent %s rejected: %s", intent.key,
                     [c.name for c in result.checks if not c.passed])
            return ExecutionResult(result, None)

        bracket = intent.stop_loss is not None and intent.take_profit is not None
        rec = OrderRecord(
            id=str(uuid.uuid4()),
            intent_key=intent.key,
            account_id=intent.account_id,
            client_order_id=client_order_id(intent.key),
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type="market" if limit is None else "limit",
            order_class="bracket" if bracket else "simple",
            limit_price=limit,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            time_in_force=intent.time_in_force,
            submitted_at=now,
            updated_at=now,
        )
        rec.status = transition(rec.status, S.SUBMITTED)
        await self.store.add_order(rec)
        if intent.rule_id and intent.cooldown_minutes:
            await self.store.set_cooldown(intent.account_id, intent.rule_id, intent.symbol,
                                          cooldown_until(now, intent.cooldown_minutes))
        req = OrderRequest(
            symbol=intent.symbol,
            side="buy" if intent.side in BUY_SIDES else "sell",
            qty=intent.qty,
            order_type=rec.order_type,
            limit_price=limit,
            time_in_force=intent.time_in_force,
            order_class=rec.order_class,
            take_profit=intent.take_profit if bracket else None,
            stop_loss=intent.stop_loss if bracket else None,
            client_order_id=rec.client_order_id,
            extended_hours=intent.extended_hours,
        )
        try:
            bo = await self.broker.submit_order(req)
        except Exception:
            log.exception("submit failed for %s", intent.key)
            rec.status = transition(rec.status, S.BROKER_REJECTED)
            await self._save(rec)
            return ExecutionResult(result, rec)
        rec.broker_order_id = bo.broker_order_id
        self._apply_status(rec, bo)
        await self._save(rec)
        return ExecutionResult(result, rec)

    def _apply_status(self, rec: OrderRecord, bo: BrokerOrder) -> bool:
        """Move rec toward the broker's status. Broker state wins except over terminal states."""
        new = normalize_broker_status(bo.status)
        before = (rec.status, rec.filled_qty)
        try:
            rec.status = transition(rec.status, new)
        except InvalidTransition:
            if not rec.status.terminal:
                log.warning("forcing %s %s -> %s", rec.id, rec.status, new)
                rec.status = new
        if bo.filled_qty >= rec.filled_qty:
            rec.filled_qty = bo.filled_qty
            rec.filled_avg_price = bo.filled_avg_price
        rec.updated_at = bo.updated_at or self.clock()
        return (rec.status, rec.filled_qty) != before

    async def handle_update(self, u: TradeUpdate) -> None:
        rec = await self.store.find_order(u.order.broker_order_id, u.order.client_order_id)
        if rec is None:
            log.warning("trade update for unknown order %s", u.order.broker_order_id)
            return
        if rec.broker_order_id is None:
            rec.broker_order_id = u.order.broker_order_id
        self._apply_status(rec, u.order)
        await self.store.update_order(rec)
        if u.fill_id and u.fill_qty and u.fill_price is not None:
            signed = u.fill_qty if u.order.side == "buy" else -u.fill_qty
            await self.store.add_fill(FillRecord(
                rec.id, rec.account_id, u.fill_id, rec.symbol, signed, u.fill_price,
                u.at or self.clock(),
            ))
        await self._notify(rec)  # after the fill, so listeners that reload trades see it

    async def drain_updates(self) -> int:
        """Process queued updates (in-memory brokers). Live code uses run_stream instead."""
        queue = getattr(self.broker, "updates", None)
        n = 0
        while queue is not None and not queue.empty():
            await self.handle_update(queue.get_nowait())
            n += 1
        return n

    async def run_stream(self) -> None:
        async for u in self.broker.stream_trade_updates():
            try:
                await self.handle_update(u)
            except Exception:
                log.exception("failed to handle trade update")

    async def reconcile(self, account_id: str) -> list[str]:
        """Bring open local orders in line with the broker. Returns ids of changed orders."""
        open_orders = await self.store.open_orders(account_id)
        if not open_orders:
            return []
        since = min((o.submitted_at or self.clock()) for o in open_orders)
        by_id = {o.broker_order_id: o for o in await self.broker.list_orders(since)}
        by_coid = {o.client_order_id: o for o in by_id.values()}
        changed = []
        for rec in open_orders:
            bo = (by_id.get(rec.broker_order_id) if rec.broker_order_id else None) or by_coid.get(
                rec.client_order_id
            )
            if bo is None:
                continue
            rec.broker_order_id = bo.broker_order_id
            if self._apply_status(rec, bo):
                await self._save(rec)
                changed.append(rec.id)
        return changed

    async def set_kill_switch(self, account_id: str, on: bool, flatten: bool = False) -> None:
        await self.store.set_kill_switch(account_id, on)
        if on:
            await self.broker.cancel_all()
            if flatten:
                await self.broker.close_all_positions()
