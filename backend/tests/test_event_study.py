from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.analytics.lots import Fill, match_fills
from app.services.backtest.engine import NewsEvent
from app.services.backtest.event_study import (
    clustered_t,
    event_study,
    lead_lag,
    source_credibility,
)
from app.services.backtest.trade_compare import compare_trades
from app.services.brokers.base import PriceBar

T0 = datetime(2024, 1, 2, 5, tzinfo=UTC)
DUR = timedelta(hours=16)


def mk(closes, start=T0):
    return [PriceBar(start + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6)
            for i, c in enumerate(closes)]


def market(n=400, seed=0, jumps=()):
    """Bench and a stock with beta 1 plus jumps right after given event days."""
    rng = np.random.default_rng(seed)
    rb = rng.normal(0, 0.01, n)
    rs = rb + rng.normal(0, 0.002, n)
    for d, j in jumps:
        rs[d + 1] += j
    bench = 100 * np.cumprod(1 + rb)
    stock = 50 * np.cumprod(1 + rs)
    return mk(list(bench)), mk(list(stock))


def test_clustered_t_matches_iid_when_one_per_cluster():
    x = np.array([1.0, 2, 3, 4, 5, 6])
    t = clustered_t(x, clusters=list(range(6)))
    se = np.sqrt(np.sum((x - x.mean()) ** 2) / len(x) ** 2 * 6 / 5)
    assert t == pytest.approx(x.mean() / se)


def test_positive_news_followed_by_jumps_is_detected():
    days = list(range(80, 380, 5))
    bench, stock = market(jumps=[(d, 0.03) for d in days])
    events = [NewsEvent("S", T0 + timedelta(days=d, hours=22), impact=0.8, event_type="earnings",
                        source="wire", sentiment=0.8) for d in days]
    res = event_study(events, {"S": stock}, bench, horizons={"1d": 1, "5d": 5}, bar_duration=DUR)
    g = next(r for r in res.groups if r.event_type == "earnings" and r.horizon == "1d")
    assert g.n == len(days)
    assert g.mean_car == pytest.approx(0.03, abs=0.005)
    assert g.t_stat > 5
    assert g.hit_rate > 0.9
    assert any(p.event_type == "earnings" for p in res.patterns)


def test_noise_news_produces_no_pattern():
    bench, stock = market(seed=3)
    rng = np.random.default_rng(4)
    events = [NewsEvent("S", T0 + timedelta(days=int(d), hours=20), impact=0.5,
                        event_type="product", source="blog", sentiment=float(rng.choice([-1, 1])))
              for d in rng.choice(np.arange(70, 390), 60, replace=False)]
    res = event_study(events, {"S": stock}, bench, horizons={"1d": 1}, bar_duration=DUR)
    assert res.patterns == []


def test_source_credibility_rewards_hit_rate():
    w = source_credibility({"good": (0.8, 100), "bad": (0.4, 100), "thin": (0.9, 5)})
    assert w["good"] > 0.9 and w["bad"] < 0.5
    assert "thin" not in w


def test_lead_lag_finds_one_bar_lead():
    rng = np.random.default_rng(5)
    s = rng.normal(size=500)
    r = np.roll(s, 1) * 0.5 + rng.normal(0, 0.1, 500)
    cc = lead_lag(s, r, max_lag=3)
    assert max(cc, key=cc.get) == 1


def test_compare_trades_alternatives_and_efficiency():
    closes = [100.0] * 30 + [100 + i for i in range(1, 41)]
    bars = mk(closes)
    bench = mk([200.0] * 70)
    entry = bars[30].ts
    fills = [Fill("a", "S", 10, 101, entry), Fill("b", "S", -10, 103, bars[32].ts)]
    trades = match_fills(fills)
    rep = compare_trades(trades, {"S": bars}, bench, holds=(1, 5, 20), seed=0)
    (row,) = rep.rows
    assert row.actual_return == pytest.approx(2 / 101)
    assert row.alternatives["hold_5"] == pytest.approx(closes[35] / 101 - 1)
    assert row.alternatives["benchmark"] == pytest.approx(0.0)
    assert 0 < row.exit_efficiency < 1
    lo, mean, hi = rep.mean_diff["hold_20"]
    assert lo <= mean <= hi and mean > 0
