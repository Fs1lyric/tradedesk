from datetime import UTC, datetime

import pytest

from app.services.brokers.fake import FakeBroker
from app.services.execution.service import (
    ExecutionService,
    InMemoryExecutionStore,
    TradeIntent,
    client_order_id,
)
from app.services.execution.state import OrderStatus
from app.services.risk.manager import RiskLimits, RiskManager

NOW = datetime(2026, 3, 2, 15, tzinfo=UTC)


@pytest.fixture
def setup():
    broker = FakeBroker(cash=100_000, now=NOW)
    broker.set_quote("AAPL", 99.95, 100.05)
    store = InMemoryExecutionStore()
    svc = ExecutionService(broker, RiskManager(RiskLimits()), store, clock=lambda: NOW)
    return broker, store, svc


def buy(key="k1", qty=40, **kw):
    return TradeIntent(key=key, account_id="acct", symbol="AAPL", side="buy", qty=qty, **kw)


async def test_marketable_limit_bracket_is_submitted_and_filled(setup):
    broker, store, svc = setup
    res = await svc.execute(buy(stop_loss=96.0, take_profit=106.0))
    assert res.risk.passed
    assert res.order is not None
    req = broker.orders[res.order.broker_order_id]
    assert req.client_order_id == client_order_id("k1")
    await svc.drain_updates()
    order = store.orders[res.order.id]
    assert order.status is OrderStatus.FILLED
    assert order.order_class == "bracket"
    assert order.limit_price == pytest.approx(100.05 * 1.001, rel=1e-6)
    assert len(store.fill_list) == 1
    assert broker.positions["AAPL"][0] == 40


async def test_risk_rejection_never_reaches_broker(setup):
    broker, store, svc = setup
    res = await svc.execute(buy(qty=500))  # 50k > 10% position limit
    assert not res.risk.passed
    assert res.order is None
    assert broker.orders == {}
    assert store.intents["k1"].risk["passed"] is False


async def test_same_intent_twice_is_rejected_as_duplicate(setup):
    _, _, svc = setup
    await svc.execute(buy())
    res = await svc.execute(buy())
    assert "duplicate" in {c.name for c in res.risk.checks if not c.passed}


async def test_broker_rejection_is_recorded(setup):
    broker, store, svc = setup
    broker.reject_next = True
    res = await svc.execute(buy())
    await svc.drain_updates()
    assert store.orders[res.order.id].status is OrderStatus.BROKER_REJECTED


async def test_kill_switch_cancels_and_blocks(setup):
    broker, store, svc = setup
    broker.fill_immediately = False
    res = await svc.execute(buy())
    await svc.set_kill_switch("acct", True)
    await svc.drain_updates()
    assert store.orders[res.order.id].status is OrderStatus.CANCELED
    blocked = await svc.execute(buy(key="k2"))
    assert not blocked.risk.passed


async def test_reconcile_fixes_missed_updates(setup):
    broker, store, svc = setup
    broker.fill_immediately = False
    res = await svc.execute(buy())
    await svc.drain_updates()
    await broker.fill(res.order.broker_order_id, 40, 100.05)
    while not broker.updates.empty():  # simulate a dropped stream
        broker.updates.get_nowait()
    fixed = await svc.reconcile("acct")
    assert fixed == [res.order.id]
    assert store.orders[res.order.id].status is OrderStatus.FILLED


async def test_daily_loss_halts_account(setup):
    broker, store, svc = setup
    broker.last_equity = 110_000  # down 9% today
    res = await svc.execute(buy())
    assert not res.risk.passed
    assert await store.is_halted("acct", NOW)


async def test_short_maps_to_sell(setup):
    broker, store, svc = setup
    res = await svc.execute(TradeIntent(key="s", account_id="acct", symbol="AAPL",
                                        side="short", qty=10))
    assert broker.orders[res.order.broker_order_id].side == "sell"
    await svc.drain_updates()
    assert broker.positions["AAPL"][0] == -10
