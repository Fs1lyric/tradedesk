import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.events import order_channel
from app.models import EquitySnapshot, Order, Recommendation
from app.runtime import Runtime
from app.services.brokers.fake import FakeBroker
from app.workers.jobs.accounts import expire_recommendations, reconcile_all, snapshot_equity
from tests.conftest import requires_db, signup

pytestmark = requires_db

ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}


@pytest.fixture
async def desk(api):
    headers = await signup(api)
    acct = (await api.post("/accounts", headers=headers, json=ACCOUNT)).json()["id"]
    broker = FakeBroker(cash=50_000)
    broker.fill_immediately = False
    broker.set_quote("AAPL", 99.95, 100.05)
    api.app.state.broker_factory = lambda account, settings: broker
    return headers, acct, broker, Runtime.from_app_state(api.app.state)


async def test_manual_order_is_published(api, desk):
    headers, acct, _, rt = desk
    async with rt.bus.subscribe([order_channel(acct)]) as stream:
        r = await api.post(f"/accounts/{acct}/orders", headers=headers,
                           json={"symbol": "AAPL", "side": "buy", "qty": 1})
        event = await asyncio.wait_for(anext(stream), 2)
    assert event["type"] == "order"
    assert event["order"]["id"] == r.json()["order"]["id"]
    assert event["order"]["status"] == "ACCEPTED" or event["order"]["status"] == "SUBMITTED"


async def test_reconcile_all_publishes_changes(api, desk):
    headers, acct, broker, rt = desk
    r = await api.post(f"/accounts/{acct}/orders", headers=headers,
                       json={"symbol": "AAPL", "side": "buy", "qty": 2})
    oid = r.json()["order"]["id"]
    await broker.fill(list(broker.orders)[-1], 2, 100.0)
    async with rt.bus.subscribe([order_channel(acct)]) as stream:
        changed = await reconcile_all(rt)
        event = await asyncio.wait_for(anext(stream), 2)
    assert changed == {acct: [oid]}
    assert event["order"]["status"] == "FILLED"
    async with rt.sessions() as s:
        assert (await s.get(Order, oid)).status == "FILLED"


async def test_snapshot_equity(api, desk):
    _, acct, _, rt = desk
    now = datetime(2026, 3, 2, 21, tzinfo=UTC)
    assert await snapshot_equity(rt, now) == 1
    async with rt.sessions() as s:
        snap = await s.scalar(select(EquitySnapshot))
    assert snap.account_id == acct and snap.equity == 50_000 and snap.ts == now
    assert await snapshot_equity(rt, now) == 1  # same ts again is an upsert, not an error


async def test_snapshot_includes_kill_switched_accounts(api, desk):
    headers, acct, _, rt = desk
    await api.patch(f"/accounts/{acct}", headers=headers, json={"kill_switch": True})
    assert await snapshot_equity(rt, datetime.now(UTC)) == 1


async def test_expire_recommendations(api, desk):
    _, acct, _, rt = desk
    now = datetime.now(UTC)
    async with rt.sessions.begin() as s:
        for minutes in (-5, 5):
            s.add(Recommendation(
                account_id=acct, symbol="AAPL", side="buy", qty=1, entry_price=100,
                expected_return=0.01, score=1, confidence=1,
                expires_at=now + timedelta(minutes=minutes),
            ))
    assert await expire_recommendations(rt.sessions, now) == 1
    async with rt.sessions() as s:
        statuses = sorted((await s.scalars(select(Recommendation.status))).all())
    assert statuses == ["expired", "pending"]
