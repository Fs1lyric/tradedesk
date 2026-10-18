"""One contract for every ExecutionStore: the in-memory one and the Postgres one."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db import make_engine, make_sessionmaker
from app.models import BrokerAccount, User
from app.services.brokers.fake import FakeBroker
from app.services.execution.service import (
    ExecutionService,
    ExecutionStore,
    FillRecord,
    InMemoryExecutionStore,
    IntentRecord,
    OrderRecord,
    TradeIntent,
)
from app.services.execution.sql_store import SqlExecutionStore
from app.services.execution.state import OrderStatus
from app.services.risk.manager import RiskLimits, RiskManager
from tests.conftest import DATABASE_URL

NOW = datetime(2026, 3, 2, 15, tzinfo=UTC)
ACCT = str(uuid.UUID(int=1))
OTHER = str(uuid.UUID(int=2))

STORES = ["memory", pytest.param("sql", marks=pytest.mark.skipif(
    DATABASE_URL is None, reason="DATABASE_URL not set"))]


@pytest.fixture(params=STORES)
async def store(request) -> AsyncIterator[ExecutionStore]:
    if request.param == "memory":
        yield InMemoryExecutionStore()
        return
    request.getfixturevalue("migrated_db")
    engine = make_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, broker_accounts CASCADE"))
    sessions = make_sessionmaker(engine)
    async with sessions.begin() as s:
        s.add(User(id=str(uuid.UUID(int=9)), email="t@example.com", password_hash="x"))
        await s.flush()
        for acct in (ACCT, OTHER):
            s.add(BrokerAccount(id=acct, user_id=str(uuid.UUID(int=9)), broker="alpaca",
                                label=acct, api_key_enc=b"k", api_secret_enc=b"s"))
    yield SqlExecutionStore(sessions)
    await engine.dispose()


def order(n: int = 1, account: str = ACCT, **kw) -> OrderRecord:
    base = dict(
        id=str(uuid.UUID(int=100 + n)), intent_key=f"k{n}", account_id=account,
        client_order_id=f"c{n}", symbol="AAPL", side="buy", qty=10.0, order_type="limit",
        order_class="simple", limit_price=100.0, stop_loss=None, take_profit=None,
        time_in_force="day", status=OrderStatus.SUBMITTED, submitted_at=NOW, updated_at=NOW,
    )
    return OrderRecord(**(base | kw))


def fill(n: int, order_id: str, at: datetime = NOW, account: str = ACCT) -> FillRecord:
    return FillRecord(order_id, account, f"f{n}", "AAPL", 10.0, 100.0, at, 0.1)


async def test_find_order_by_either_id_and_update_persists(store):
    await store.add_order(order())
    rec = await store.find_order(None, "c1")
    assert rec is not None and rec.status is OrderStatus.SUBMITTED
    rec.broker_order_id = "b1"
    rec.status = OrderStatus.FILLED
    rec.filled_qty, rec.filled_avg_price = 10.0, 100.02
    await store.update_order(rec)
    got = await store.find_order("b1", None)
    assert got is not None
    assert (got.status, got.filled_qty, got.filled_avg_price) == (OrderStatus.FILLED, 10.0, 100.02)
    assert got.submitted_at == NOW
    assert await store.find_order("nope", "nope") is None


async def test_open_orders_skip_terminal_and_other_accounts(store):
    await store.add_order(order(1))
    await store.add_order(order(2, status=OrderStatus.CANCELED))
    await store.add_order(order(3, account=OTHER))
    assert [o.client_order_id for o in await store.open_orders(ACCT)] == ["c1"]


async def test_fills_are_idempotent_and_filtered_by_time(store):
    await store.add_order(order())
    oid = order().id
    assert await store.add_fill(fill(1, oid, NOW - timedelta(days=2)))
    assert await store.add_fill(fill(2, oid))
    assert not await store.add_fill(fill(2, oid))
    recent = await store.fills(ACCT, NOW - timedelta(hours=1))
    assert [f.broker_fill_id for f in recent] == ["f2"]
    assert recent[0].filled_at == NOW and recent[0].fee == pytest.approx(0.1)
    assert await store.fills(OTHER, NOW - timedelta(days=9)) == []


async def test_intents_can_repeat_and_feed_duplicate_keys(store):
    intent = TradeIntent(key="k1", account_id=ACCT, symbol="AAPL", side="buy", qty=1)
    await store.save_intent(IntentRecord(intent, {"passed": True}, NOW))
    await store.save_intent(IntentRecord(intent, {"passed": False}, NOW))
    assert await store.recent_intent_keys(ACCT) == {"k1"}
    assert await store.recent_intent_keys(OTHER) == set()


async def test_order_times_since(store):
    await store.add_order(order(1, submitted_at=NOW - timedelta(days=1)))
    await store.add_order(order(2, submitted_at=NOW))
    assert await store.order_times(ACCT, NOW - timedelta(hours=1)) == [NOW]


async def test_cooldowns_overwrite(store):
    await store.set_cooldown(ACCT, "r1", "AAPL", NOW)
    await store.set_cooldown(ACCT, "r1", "AAPL", NOW + timedelta(hours=1))
    assert await store.cooldowns(ACCT) == {("r1", "AAPL"): NOW + timedelta(hours=1)}
    assert await store.cooldowns(OTHER) == {}


async def test_halt_lasts_one_day(store):
    assert not await store.is_halted(ACCT, NOW)
    await store.set_halted(ACCT, NOW)
    await store.set_halted(ACCT, NOW)
    assert await store.is_halted(ACCT, NOW + timedelta(hours=1))
    assert not await store.is_halted(ACCT, NOW + timedelta(days=1))
    assert not await store.is_halted(OTHER, NOW)


async def test_kill_switch_toggles(store):
    assert not await store.kill_switch(ACCT)
    await store.set_kill_switch(ACCT, True)
    assert await store.kill_switch(ACCT)
    await store.set_kill_switch(ACCT, False)
    assert not await store.kill_switch(ACCT)


async def test_execution_service_round_trip(store):
    broker = FakeBroker(cash=100_000, now=NOW)
    broker.set_quote("AAPL", 99.95, 100.05)
    svc = ExecutionService(broker, RiskManager(RiskLimits()), store, clock=lambda: NOW)
    intent = TradeIntent(key="e2e", account_id=ACCT, symbol="AAPL", side="buy", qty=40,
                         stop_loss=96.0, take_profit=106.0)
    res = await svc.execute(intent)
    assert res.order is not None
    await svc.drain_updates()
    rec = await store.find_order(res.order.broker_order_id, None)
    assert rec is not None and rec.status is OrderStatus.FILLED
    assert len(await store.fills(ACCT, NOW - timedelta(minutes=1))) == 1
    again = await svc.execute(intent)
    assert "duplicate" in {c.name for c in again.risk.checks if not c.passed}
