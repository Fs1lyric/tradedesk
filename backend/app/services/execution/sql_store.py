"""Postgres ExecutionStore. Each call runs in its own short transaction."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BrokerAccount, Cooldown, FillRow, Order, TradeIntentRow, TradingHalt
from app.services.execution.service import FillRecord, IntentRecord, OrderRecord
from app.services.execution.state import TERMINAL, OrderStatus

# Duplicate-intent window. Older keys can't resubmit anyway: client_order_id is unique.
INTENT_KEY_WINDOW = timedelta(days=7)

_ORDER_FIELDS = (
    "intent_key", "account_id", "client_order_id", "symbol", "side", "qty", "order_type",
    "order_class", "limit_price", "stop_loss", "take_profit", "time_in_force",
    "broker_order_id", "filled_qty", "filled_avg_price", "submitted_at", "updated_at",
)


def _order_row(rec: OrderRecord) -> dict[str, object]:
    row: dict[str, object] = {f: getattr(rec, f) for f in _ORDER_FIELDS}
    row["id"] = rec.id
    row["status"] = rec.status.value
    return row


def _order_rec(row: Order) -> OrderRecord:
    return OrderRecord(id=row.id, status=OrderStatus(row.status),
                       **{f: getattr(row, f) for f in _ORDER_FIELDS})


class SqlExecutionStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def save_intent(self, rec: IntentRecord) -> None:
        i = rec.intent
        async with self.sessions.begin() as s:
            s.add(TradeIntentRow(
                key=i.key, account_id=i.account_id, symbol=i.symbol, side=i.side, qty=i.qty,
                order_type=i.order_type, limit_price=i.limit_price, stop_loss=i.stop_loss,
                take_profit=i.take_profit, time_in_force=i.time_in_force,
                extended_hours=i.extended_hours, origin=i.origin, rule_id=i.rule_id,
                recommendation_id=i.recommendation_id, cooldown_minutes=i.cooldown_minutes,
                risk=rec.risk, passed=bool(rec.risk.get("passed")), created_at=rec.created_at,
            ))

    async def add_order(self, rec: OrderRecord) -> None:
        async with self.sessions.begin() as s:
            await s.execute(insert(Order).values(_order_row(rec)))

    async def update_order(self, rec: OrderRecord) -> None:
        row = _order_row(rec)
        async with self.sessions.begin() as s:
            await s.execute(insert(Order).values(row).on_conflict_do_update(
                index_elements=[Order.id], set_={k: v for k, v in row.items() if k != "id"},
            ))

    async def find_order(
        self, broker_order_id: str | None, client_order_id: str | None
    ) -> OrderRecord | None:
        async with self.sessions() as s:
            for col, val in ((Order.broker_order_id, broker_order_id),
                             (Order.client_order_id, client_order_id)):
                if val:
                    row = (await s.execute(select(Order).where(col == val))).scalar_one_or_none()
                    if row is not None:
                        return _order_rec(row)
        return None

    async def open_orders(self, account_id: str) -> list[OrderRecord]:
        q = (select(Order)
             .where(Order.account_id == account_id,
                    Order.status.not_in([t.value for t in TERMINAL]))
             .order_by(Order.submitted_at))
        async with self.sessions() as s:
            return [_order_rec(r) for r in (await s.execute(q)).scalars()]

    async def add_fill(self, rec: FillRecord) -> bool:
        stmt = (insert(FillRow)
                .values(order_id=rec.order_id, account_id=rec.account_id,
                        broker_fill_id=rec.broker_fill_id, symbol=rec.symbol, qty=rec.qty,
                        price=rec.price, fee=rec.fee, filled_at=rec.filled_at)
                .on_conflict_do_nothing(index_elements=[FillRow.broker_fill_id])
                .returning(FillRow.id))
        async with self.sessions.begin() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None

    async def fills(self, account_id: str, since: datetime) -> list[FillRecord]:
        q = (select(FillRow)
             .where(FillRow.account_id == account_id, FillRow.filled_at >= since)
             .order_by(FillRow.filled_at, FillRow.id))
        async with self.sessions() as s:
            return [
                FillRecord(r.order_id, r.account_id, r.broker_fill_id, r.symbol, r.qty, r.price,
                           r.filled_at, r.fee)
                for r in (await s.execute(q)).scalars()
            ]

    async def recent_intent_keys(self, account_id: str) -> set[str]:
        # The window is anchored on the newest intent, not the wall clock, so a pinned clock
        # in backtests and tests sees the same keys as live.
        async with self.sessions() as s:
            latest = (await s.execute(
                select(TradeIntentRow.created_at)
                .where(TradeIntentRow.account_id == account_id)
                .order_by(TradeIntentRow.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            if latest is None:
                return set()
            q = select(TradeIntentRow.key).distinct().where(
                TradeIntentRow.account_id == account_id,
                TradeIntentRow.created_at >= latest - INTENT_KEY_WINDOW,
            )
            return set((await s.execute(q)).scalars())

    async def order_times(self, account_id: str, since: datetime) -> list[datetime]:
        q = (select(Order.submitted_at)
             .where(Order.account_id == account_id, Order.submitted_at >= since)
             .order_by(Order.submitted_at))
        async with self.sessions() as s:
            return [t for t in (await s.execute(q)).scalars() if t is not None]

    async def cooldowns(self, account_id: str) -> dict[tuple[str, str], datetime]:
        async with self.sessions() as s:
            rows = (await s.execute(
                select(Cooldown).where(Cooldown.account_id == account_id)
            )).scalars()
            return {(r.rule_id, r.symbol): r.until for r in rows}

    async def set_cooldown(self, account_id: str, rule_id: str, symbol: str, until: datetime) -> None:  # noqa: E501
        stmt = insert(Cooldown).values(account_id=account_id, rule_id=rule_id, symbol=symbol,
                                       until=until)
        async with self.sessions.begin() as s:
            await s.execute(stmt.on_conflict_do_update(
                index_elements=[Cooldown.account_id, Cooldown.rule_id, Cooldown.symbol],
                set_={"until": until},
            ))

    async def is_halted(self, account_id: str, day: datetime) -> bool:
        async with self.sessions() as s:
            row = await s.get(TradingHalt, (account_id, day.date()))
            return row is not None

    async def set_halted(self, account_id: str, day: datetime) -> None:
        stmt = insert(TradingHalt).values(account_id=account_id, day=day.date(), halted_at=day)
        async with self.sessions.begin() as s:
            await s.execute(stmt.on_conflict_do_nothing())

    async def kill_switch(self, account_id: str) -> bool:
        async with self.sessions() as s:
            on = (await s.execute(
                select(BrokerAccount.kill_switch).where(BrokerAccount.id == account_id)
            )).scalar_one_or_none()
            return bool(on)

    async def set_kill_switch(self, account_id: str, on: bool) -> None:
        async with self.sessions.begin() as s:
            acct = await s.get(BrokerAccount, account_id)
            if acct is None:
                raise KeyError(account_id)
            acct.kill_switch = on
