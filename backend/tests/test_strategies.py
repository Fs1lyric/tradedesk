from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import BacktestConfig, Backtester, NewsEvent
from app.services.backtest.strategies import STRATEGIES, SentimentTracker, build_strategy
from app.services.brokers.base import PriceBar

T0 = datetime(2024, 1, 2, 5, tzinfo=UTC)


def series(n=400, drift=0.001, vol=0.01, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    out = []
    for i, c in enumerate(close):
        o = close[i - 1] if i else c
        out.append(PriceBar(T0 + timedelta(days=i), o, max(o, c) * 1.003, min(o, c) * 0.997, c,
                            2e6))
    return out


CFG = BacktestConfig(initial_cash=100_000, costs=CostModel(slippage_bps=1, spread_bps=1))


def test_registry_builds_every_strategy():
    for name in STRATEGIES:
        assert build_strategy(name, {}) is not None
    with pytest.raises(KeyError):
        build_strategy("nope", {})


def test_buy_and_hold_tracks_price():
    data = {"A": series(seed=1)}
    res = Backtester(CFG).run(build_strategy("buy_and_hold", {"weight": 0.09}), data)
    px = [b.close for b in data["A"]]
    assert len(res.fills) == 1
    expected = 0.09 * (px[-1] / data["A"][1].open - 1)
    assert res.metrics["cumulative_return"] == pytest.approx(expected, abs=0.005)


def test_composite_trades_an_uptrend_profitably():
    data = {"A": series(drift=0.003, vol=0.008, seed=2)}
    res = Backtester(CFG).run(build_strategy("composite", {"entry_threshold": 0.2}), data)
    assert len(res.trades) > 0
    assert res.metrics["cumulative_return"] > 0


def test_technical_trend_goes_flat_on_cross_down():
    up = series(n=200, drift=0.004, vol=0.005, seed=3)
    down = series(n=150, drift=-0.006, vol=0.005, seed=4, start=up[-1].close)
    down = [PriceBar(b.ts + timedelta(days=200), b.open, b.high, b.low, b.close, b.volume)
            for b in down]
    res = Backtester(CFG).run(build_strategy("technical_trend", {}), {"A": up + down})
    assert res.trades and res.trades[0].closed


def test_news_momentum_follows_and_fade_opposes():
    data = {"A": series(n=120, seed=5)}
    news = [NewsEvent("A", T0 + timedelta(days=80, hours=20), impact=0.9, sentiment=0.8,
                      event_type="earnings")]
    mom = Backtester(CFG).run(build_strategy("news_momentum", {}), data, news)
    fade = Backtester(CFG).run(build_strategy("news_fade", {}), data, news)
    assert mom.fills[0].qty > 0
    assert fade.fills[0].qty < 0


def test_sentiment_tracker_decays_and_zscores():
    tr = SentimentTracker(half_life_h=24)
    t = T0
    tr.add("A", t, 1.0)
    assert tr.value("A", t) == pytest.approx(1.0)
    assert tr.value("A", t + timedelta(hours=24)) == pytest.approx(0.5)
    for i in range(30):
        tr.sample("A", t + timedelta(days=i))
    feat, attn = tr.features("A", t + timedelta(days=30))
    assert -1 <= feat <= 1
    assert np.isfinite(attn)
