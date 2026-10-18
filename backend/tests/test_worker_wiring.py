import asyncio

import pytest
from sqlalchemy import select

from app.core.events import order_channel
from app.models import FillRow
from app.runtime import Runtime
from app.services.brokers.fake import FakeBroker
from app.workers.celery_app import app as celery
from app.workers.stream import stream_all
from tests.conftest import requires_db, signup

ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}


def test_beat_schedule_points_at_registered_tasks():
    import app.workers.tasks  # noqa: F401  registers the tasks

    names = {entry["task"] for entry in celery.conf.beat_schedule.values()}
    assert names <= set(celery.tasks)
    assert {"app.workers.tasks.ingest_news", "app.workers.tasks.evaluate_rules",
            "app.workers.tasks.run_backtests", "app.workers.tasks.snapshot_equity"} <= names


@requires_db
async def test_stream_applies_fills_and_publishes(api):
    headers = await signup(api)
    acct = (await api.post("/accounts", headers=headers, json=ACCOUNT)).json()["id"]
    broker = FakeBroker(cash=100_000)
    broker.fill_immediately = False
    broker.set_quote("AAPL", 99.95, 100.05)
    api.app.state.broker_factory = lambda account, settings: broker
    rt = Runtime.from_app_state(api.app.state)

    r = await api.post(f"/accounts/{acct}/orders", headers=headers,
                       json={"symbol": "AAPL", "side": "buy", "qty": 3})
    oid = r.json()["order"]["id"]
    async with rt.bus.subscribe([order_channel(acct)]) as stream:
        task = asyncio.create_task(stream_all(rt, refresh_s=0.05))
        try:
            await broker.fill(list(broker.orders)[-1], 3, 100.0)
            while True:
                event = await asyncio.wait_for(anext(stream), 5)
                if event["order"]["status"] == "FILLED":
                    break
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert event["order"]["id"] == oid
    async with rt.sessions() as s:
        fill = await s.scalar(select(FillRow))
    assert fill.qty == 3 and fill.price == 100.0
