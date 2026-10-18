from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from app.models import NewsItem, NewsTicker, Order, PriceBar, Recommendation, SignalSnapshot
from app.runtime import Runtime
from app.services.brokers.base import PriceBar as Bar
from app.services.brokers.fake import FakeBroker
from app.workers.jobs.market import load_bars, sync_bars, universe, upsert_bars
from app.workers.jobs.rules import run_rules
from app.workers.jobs.signals import compute_signals
from tests.conftest import requires_db, signup

pytestmark = requires_db

T0 = datetime(2025, 3, 1, 5, tzinfo=UTC)
NOW = T0 + timedelta(days=260)
ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}
RULE = {
    "name": "trend follower",
    "universe": {"symbols": ["AAPL"]},
    "when": {"feature": "signal.trend", "op": ">", "value": 0},
    "action": {"side": "buy", "order": "bracket"},
    "cooldown_minutes": 60,
}


def uptrend(n: int = 250, seed: int = 1) -> list[Bar]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(0.003 + 0.004 * rng.standard_normal(n)))
    return [Bar(T0 + timedelta(days=i), c * 0.998, c * 1.01, c * 0.99, c, 1_000_000)
            for i, c in enumerate(close)]


class FakeData:
    def __init__(self, bars: dict[str, list[Bar]]):
        self.bars = bars

    async def get_bars(self, symbols, timeframe, start, end):
        return {s: self.bars.get(s, []) for s in symbols}


async def test_bars_roundtrip_and_upsert(api):
    sessions = api.app.state.sessions
    bars = uptrend(10)
    assert await sync_bars(sessions, FakeData({"AAPL": bars}), ["AAPL"], "1Day",
                           T0, NOW) == 10
    assert await upsert_bars(sessions, "1Day", {"AAPL": bars[:3]}) == 3  # re-sync is an upsert
    loaded = await load_bars(sessions, ["AAPL", "MSFT"], "1Day")
    assert [b.close for b in loaded["AAPL"]] == pytest.approx([b.close for b in bars])
    assert loaded["MSFT"] == []
    end = await load_bars(sessions, ["AAPL"], "1Day", end=bars[4].ts)
    assert len(end["AAPL"]) == 5


@pytest.fixture
async def market(api):
    headers = await signup(api)
    acct = (await api.post("/accounts", headers=headers, json=ACCOUNT)).json()["id"]
    sessions = api.app.state.sessions
    bars = uptrend()
    await upsert_bars(sessions, "1Day", {"AAPL": bars})
    broker = FakeBroker(cash=100_000)
    broker.set_quote("AAPL", bars[-1].close - 0.01, bars[-1].close + 0.01)
    api.app.state.broker_factory = lambda account, settings: broker
    return headers, acct, broker, Runtime.from_app_state(api.app.state), bars


async def test_universe_from_watchlists_and_rules(api, market):
    headers, acct, *_ = market
    wl = (await api.post("/watchlists", headers=headers, json={"name": "x"})).json()["id"]
    await api.put(f"/watchlists/{wl}/symbols/MSFT", headers=headers)
    await api.post(f"/accounts/{acct}/rules", headers=headers, json=RULE)
    assert await universe(api.app.state.sessions) == ["AAPL", "MSFT"]


async def test_compute_signals(api, market):
    *_, rt, bars = market
    async with rt.sessions.begin() as s:
        item = NewsItem(url="u", url_canonical="u", title="t",
                        available_at=NOW - timedelta(hours=1), simhash=1, event_type="earnings",
                        event_mult=1.5, sentiment=0.8,
                        novelty=1.0)
        s.add(item)
        await s.flush()
        s.add(NewsTicker(news_id=item.id, symbol="AAPL", confidence=1, relevance=1, novelty=1,
                         impact=0.6))
    snaps = await compute_signals(rt.sessions, ["AAPL", "NOPE"], NOW)
    assert [s.symbol for s in snaps] == ["AAPL"]
    snap = snaps[0]
    f = snap.features
    assert f["close"] == pytest.approx(bars[-1].close)
    assert f["trend"] > 0 and f["regime_long"] == 1.0
    assert f["sentiment"] > 0 and f["news_count_24h"] == 1
    assert snap.expected_return > 0 and snap.support > 0
    assert all(v is None or np.isfinite(v) for v in f.values())
    async with rt.sessions() as s:
        assert len((await s.scalars(select(SignalSnapshot))).all()) == 1


async def test_approve_rule_creates_one_sized_recommendation(api, market):
    headers, acct, broker, rt, bars = market
    await api.post(f"/accounts/{acct}/rules", headers=headers, json=RULE)
    await compute_signals(rt.sessions, ["AAPL"], NOW)
    made = await run_rules(rt, NOW)
    assert len(made) == 1
    assert await run_rules(rt, NOW) == []  # still pending: no duplicate
    async with rt.sessions() as s:
        rec = await s.get(Recommendation, made[0])
    assert rec.status == "pending" and rec.symbol == "AAPL" and rec.side == "buy"
    assert rec.qty >= 1
    assert rec.stop_loss < rec.entry_price < rec.take_profit
    assert rec.expires_at > NOW
    assert rec.rationale["rule"] == "trend follower"
    assert broker.orders == {}


async def test_auto_rule_executes_and_cools_down(api, market):
    headers, acct, broker, rt, _ = market
    await api.post(f"/accounts/{acct}/rules", headers=headers, json={**RULE, "mode": "auto"})
    now = datetime.now(UTC)  # the execution service checks cooldowns on the wall clock
    await compute_signals(rt.sessions, ["AAPL"], now)
    made = await run_rules(rt, now)
    assert len(made) == 1
    async with rt.sessions() as s:
        assert (await s.get(Recommendation, made[0])).status == "executed"
        assert (await s.scalar(select(Order))).symbol == "AAPL"
    assert len(broker.orders) == 1
    assert await run_rules(rt, now) == []  # cooling down


async def test_rules_skip_kill_switched_and_disabled(api, market):
    headers, acct, _, rt, _ = market
    rule = (await api.post(f"/accounts/{acct}/rules", headers=headers, json=RULE)).json()
    await compute_signals(rt.sessions, ["AAPL"], NOW)
    await api.patch(f"/rules/{rule['id']}", headers=headers, json={"enabled": False})
    assert await run_rules(rt, NOW) == []
    await api.patch(f"/rules/{rule['id']}", headers=headers, json={"enabled": True})
    await api.patch(f"/accounts/{acct}", headers=headers, json={"kill_switch": True})
    assert await run_rules(rt, NOW) == []


async def test_rule_not_matching(api, market):
    headers, acct, _, rt, _ = market
    rule = {**RULE, "when": {"feature": "signal.trend", "op": "<", "value": 0}}
    await api.post(f"/accounts/{acct}/rules", headers=headers, json=rule)
    await compute_signals(rt.sessions, ["AAPL"], NOW)
    assert await run_rules(rt, NOW) == []
    async with rt.sessions() as s:
        assert (await s.scalars(select(PriceBar))).first() is not None
