from datetime import UTC, datetime, timedelta

import pytest

from app.models import EquitySnapshot
from app.services.brokers.fake import FakeBroker
from app.services.execution.service import ExecutionService
from app.services.execution.sql_store import SqlExecutionStore
from app.services.risk.manager import RiskLimits, RiskManager
from tests.conftest import requires_db, signup

pytestmark = requires_db

ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}


@pytest.fixture
async def desk(api):
    """A signed-in user with one account wired to a FakeBroker quoting AAPL at 100."""
    headers = await signup(api)
    acct = (await api.post("/accounts", headers=headers, json=ACCOUNT)).json()["id"]
    broker = FakeBroker(cash=100_000)
    broker.fill_immediately = False  # tests fill orders themselves
    broker.set_quote("AAPL", 99.95, 100.05)
    api.app.state.broker_factory = lambda account, settings: broker
    return headers, acct, broker


async def _drain(api, broker: FakeBroker) -> None:
    """Feed the fake broker's trade updates through the execution service, as the stream
    worker will."""
    store = SqlExecutionStore(api.app.state.sessions)
    await ExecutionService(broker, RiskManager(RiskLimits()), store).drain_updates()


async def _fill_last(api, broker: FakeBroker, price: float) -> None:
    oid = list(broker.orders)[-1]
    await broker.fill(oid, broker.orders[oid].qty, price)
    await _drain(api, broker)


async def test_manual_order_is_idempotent(api, desk):
    headers, acct, broker = desk
    body = {"symbol": "aapl", "side": "buy", "qty": 10, "idempotency_key": "click-1"}
    r = await api.post(f"/accounts/{acct}/orders", headers=headers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["risk"]["passed"] is True
    order = r.json()["order"]
    assert order["symbol"] == "AAPL" and order["order_type"] == "limit"
    assert order["limit_price"] > 100.05

    again = await api.post(f"/accounts/{acct}/orders", headers=headers, json=body)
    assert again.json()["order"] is None
    assert "duplicate" in [c["name"] for c in again.json()["risk"]["checks"] if not c["passed"]]
    assert len(broker.orders) == 1


async def test_order_validation(api, desk):
    headers, acct, _ = desk
    url = f"/accounts/{acct}/orders"
    assert (await api.post(url, headers=headers,
                           json={"symbol": "AAPL", "side": "buy", "qty": 0})).status_code == 422
    assert (await api.post(url, headers=headers,
                           json={"symbol": "AAPL", "side": "hold", "qty": 1})).status_code == 422
    market_with_limit = {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market",
                         "limit_price": 100}
    assert (await api.post(url, headers=headers, json=market_with_limit)).status_code == 422
    bob = await signup(api, email="b@example.com")
    body = {"symbol": "AAPL", "side": "buy", "qty": 1}
    assert (await api.post(url, headers=bob, json=body)).status_code == 404


async def test_orders_list_cancel_and_reconcile(api, desk):
    headers, acct, broker = desk
    body = {"symbol": "AAPL", "side": "buy", "qty": 5}
    order = (await api.post(f"/accounts/{acct}/orders", headers=headers, json=body)).json()
    order = order["order"]
    listed = (await api.get(f"/accounts/{acct}/orders", headers=headers)).json()
    assert [o["id"] for o in listed] == [order["id"]]

    r = await api.post(f"/orders/{order['id']}/cancel", headers=headers)
    assert r.status_code == 202
    r = await api.post(f"/accounts/{acct}/reconcile", headers=headers)
    assert r.status_code == 200 and r.json()["changed"] == [order["id"]]
    assert (await api.get(f"/accounts/{acct}/orders", headers=headers)).json() == []
    every = (await api.get(f"/accounts/{acct}/orders?status=all", headers=headers)).json()
    assert every[0]["status"] == "CANCELED"
    assert (await api.post(f"/orders/{order['id']}/cancel", headers=headers)).status_code == 409

    bob = await signup(api, email="b@example.com")
    assert (await api.post(f"/orders/{order['id']}/cancel", headers=bob)).status_code == 404


async def test_kill_switch_cancels_and_flattens(api, desk):
    headers, acct, broker = desk
    await api.post(f"/accounts/{acct}/orders", headers=headers,
                   json={"symbol": "AAPL", "side": "buy", "qty": 10})
    await _fill_last(api, broker, 100.0)
    await api.post(f"/accounts/{acct}/orders", headers=headers,
                   json={"symbol": "AAPL", "side": "buy", "qty": 1})

    r = await api.post(f"/accounts/{acct}/kill", headers=headers, json={"flatten": True})
    assert r.status_code == 200 and r.json()["kill_switch"] is True
    statuses = [o.status for o in broker.orders.values()]
    assert statuses[1] == "canceled"
    assert broker.orders[list(broker.orders)[-1]].side == "sell"  # the flattening order
    r = await api.post(f"/accounts/{acct}/orders", headers=headers,
                       json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.json()["order"] is None


async def test_trades_from_fills(api, desk):
    headers, acct, broker = desk
    url = f"/accounts/{acct}/orders"
    await api.post(url, headers=headers, json={"symbol": "AAPL", "side": "buy", "qty": 10})
    await _fill_last(api, broker, 100.0)
    await api.post(url, headers=headers, json={"symbol": "AAPL", "side": "sell", "qty": 10})
    await _fill_last(api, broker, 104.0)
    await api.post(url, headers=headers, json={"symbol": "AAPL", "side": "buy", "qty": 3})
    await _fill_last(api, broker, 101.0)

    trades = (await api.get(f"/accounts/{acct}/trades", headers=headers)).json()
    assert [(t["direction"], t["closed"]) for t in trades] == [("long", True), ("long", False)]
    assert trades[0]["realized_pnl"] == pytest.approx(40.0)
    assert trades[0]["entry_price"] == 100.0 and trades[0]["exit_price"] == 104.0
    open_only = (await api.get(f"/accounts/{acct}/trades?status=open", headers=headers)).json()
    assert len(open_only) == 1 and open_only[0]["qty"] == 3


async def test_analytics(api, desk):
    headers, acct, _ = desk
    t0 = datetime(2026, 3, 2, 21, tzinfo=UTC)
    async with api.app.state.sessions.begin() as s:
        for i, eq in enumerate([100_000, 110_000, 99_000, 120_000]):
            s.add(EquitySnapshot(account_id=acct, ts=t0 + timedelta(days=i), equity=eq,
                                 cash=eq, buying_power=eq))
    r = await api.get(f"/accounts/{acct}/analytics", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metrics"]["max_drawdown"] == pytest.approx(-0.1)
    assert body["metrics"]["cumulative_return"] == pytest.approx(0.2)
    assert [p["equity"] for p in body["equity"]] == [100_000, 110_000, 99_000, 120_000]
    assert body["equity"][2]["drawdown"] == pytest.approx(-0.1)

    empty = await api.post("/accounts", headers=headers, json={**ACCOUNT, "label": "new"})
    r = await api.get(f"/accounts/{empty.json()['id']}/analytics", headers=headers)
    assert r.status_code == 200 and r.json()["equity"] == []
