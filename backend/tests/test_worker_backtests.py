from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.models import BacktestRun, NewsItem, NewsTicker
from app.services.brokers.base import PriceBar as Bar
from app.workers.jobs.backtests import claim_next, jsonable, run_next
from app.workers.jobs.market import upsert_bars
from tests.conftest import requires_db, signup

T0 = datetime(2024, 1, 1, 5, tzinfo=UTC)


def walk(n: int, drift: float, seed: int) -> list[Bar]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n)))
    return [Bar(T0 + timedelta(days=i), c, c * 1.01, c * 0.99, c, 2_000_000)
            for i, c in enumerate(close)]


def test_jsonable():
    out = jsonable({"a": np.float64(1.5), "b": [np.nan, np.int64(2)], "c": (T0,),
                    "d": float("inf"), "e": np.array([1.0, 2.0])})
    assert out == {"a": 1.5, "b": [None, 2], "c": [T0.isoformat()], "d": None, "e": [1.0, 2.0]}


@pytest.fixture
async def queued(api):
    headers = await signup(api)
    await upsert_bars(api.app.state.sessions, "1Day",
                      {"AAPL": walk(600, 0.001, 1), "SPY": walk(600, 0.0005, 2)})

    async def queue(**kw) -> str:
        body = {"kind": "backtest", "strategy": "technical_trend", "symbols": ["AAPL"],
                "start": "2024-06-01", "end": "2025-08-01", "strategy_params": {}, **kw}
        r = await api.post("/backtests", headers=headers, json=body)
        assert r.status_code == 202, r.text
        return r.json()["id"]

    return api, headers, queue


async def _finished(api, run_id: str) -> BacktestRun:
    async with api.app.state.sessions() as s:
        return await s.get(BacktestRun, run_id)


@requires_db
async def test_backtest_run(queued):
    api, headers, queue = queued
    run_id = await queue()
    assert await run_next(api.app.state.sessions) == run_id
    assert await run_next(api.app.state.sessions) is None  # queue empty
    run = await _finished(api, run_id)
    assert run.status == "done", run.error
    assert run.finished_at is not None
    assert "sharpe" in run.metrics
    equity = run.result["equity"]
    assert equity[0][0] >= "2024-06-01"  # warm-up bars are loaded but not traded
    assert len(equity) <= 1000
    detail = (await api.get(f"/backtests/{run_id}", headers=headers)).json()
    assert detail["status"] == "done" and detail["result"]["trades"] is not None


@requires_db
async def test_claim_is_exclusive(queued):
    api, _, queue = queued
    a, b = await queue(), await queue()
    claimed = {await claim_next(api.app.state.sessions), await claim_next(api.app.state.sessions)}
    assert claimed == {a, b}
    assert await claim_next(api.app.state.sessions) is None


@requires_db
async def test_walkforward_run(queued):
    api, _, queue = queued
    run_id = await queue(kind="walkforward", start="2024-03-01", options={
        "train_bars": 150, "test_bars": 75, "n_trials": 3,
        "param_space": {"fast": [5, 20], "slow": [30, 60]},
    })
    await run_next(api.app.state.sessions)
    run = await _finished(api, run_id)
    assert run.status == "done", run.error
    assert {"deflated_sharpe", "passes_gate"} <= set(run.metrics)
    assert len(run.result["windows"]) >= 2


@requires_db
async def test_compare_and_event_study_runs(queued):
    api, _, queue = queued
    async with api.app.state.sessions.begin() as s:
        for day in range(200, 560, 10):
            item = NewsItem(url=f"u{day}", url_canonical=f"u{day}", title="t",
                            available_at=T0 + timedelta(days=day, hours=14), simhash=day,
                            event_type="earnings", event_mult=1.0,
                            sentiment=0.5 if day % 20 else -0.5, novelty=1.0)
            s.add(item)
            await s.flush()
            s.add(NewsTicker(news_id=item.id, symbol="AAPL", confidence=1, relevance=1,
                             novelty=1, impact=item.sentiment))
    cmp_id = await queue(kind="compare")
    es_id = await queue(kind="event_study", options={"horizons": {"1d": 1, "5d": 5}})
    await run_next(api.app.state.sessions)
    await run_next(api.app.state.sessions)
    cmp, es = await _finished(api, cmp_id), await _finished(api, es_id)
    assert cmp.status == "done", cmp.error
    assert "mean_diff" in cmp.result
    assert es.status == "done", es.error
    assert es.metrics["observations"] > 0
    assert es.result["groups"]


@requires_db
async def test_missing_data_fails_the_run(queued):
    api, _, queue = queued
    run_id = await queue(symbols=["ZZZZ"])
    await run_next(api.app.state.sessions)
    run = await _finished(api, run_id)
    assert run.status == "failed"
    assert "ZZZZ" in run.error
