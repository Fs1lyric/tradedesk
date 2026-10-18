from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import (
    BacktestConfig,
    Backtester,
    NewsEvent,
    SimOrder,
    Strategy,
)
from app.services.brokers.base import PriceBar

T0 = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
DAY = timedelta(days=1)
FREE = CostModel(slippage_bps=0, spread_bps=0, impact_k=0)


def bars(closes, opens=None, highs=None, lows=None, start=T0):
    opens = opens or closes
    highs = highs or [max(o, c) for o, c in zip(opens, closes, strict=True)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes, strict=True)]
    return [PriceBar(start + i * DAY, o, h, lo, c, 1e6)
            for i, (o, h, lo, c) in enumerate(zip(opens, highs, lows, closes, strict=True))]


class BuyFirstBar(Strategy):
    def __init__(self, **order_kw):
        self.done = False
        self.order_kw = order_kw

    def on_bar(self, ctx, symbol, bar):
        if not self.done:
            self.done = True
            return [SimOrder(symbol, "buy", 10, **self.order_kw)]
        return []


def run(strategy, data, cfg=None, news=None):
    cfg = cfg or BacktestConfig(initial_cash=10_000, costs=FREE, bar_duration=timedelta(hours=6.5))
    return Backtester(cfg).run(strategy, data, news or [])


def test_signal_at_close_fills_next_open_no_lookahead():
    data = {"X": bars([100, 101, 102], opens=[100, 105, 102])}
    res = run(BuyFirstBar(), data)
    (fill,) = res.fills
    assert fill.price == 105  # next bar's open, not the signal bar's close
    assert fill.filled_at == T0 + DAY
    assert res.equity[-1] == pytest.approx(10_000 + 10 * (102 - 105))


def test_bracket_stop_has_priority_when_bar_spans_both():
    data = {"X": bars([100, 100, 100, 100], opens=[100, 100, 100, 100],
                      highs=[100, 100, 120, 100], lows=[100, 100, 80, 100])}
    res = run(BuyFirstBar(stop=95, target=110), data)
    (t,) = res.trades
    assert t.exit_price == 95


def test_gap_through_stop_fills_at_open():
    data = {"X": bars([100, 100, 90], opens=[100, 100, 90], highs=[100, 100, 91],
                      lows=[100, 100, 89])}
    res = run(BuyFirstBar(stop=95, target=110), data)
    assert res.trades[0].exit_price == 90


def test_time_stop_exits_next_open():
    data = {"X": bars([100, 100, 101, 102, 103], opens=[100, 100, 101, 102, 104])}
    res = run(BuyFirstBar(time_stop_bars=2), data)
    (t,) = res.trades
    assert t.closed
    assert t.exit_price == 102  # filled at bar 1 open, held bars 1-2, exits at bar 3 open


def test_costs_are_adverse():
    costs = CostModel(slippage_bps=10, spread_bps=0, impact_k=0, commission_per_share=0.01)
    cfg = BacktestConfig(initial_cash=10_000, costs=costs, bar_duration=timedelta(hours=6.5))
    res = run(BuyFirstBar(), {"X": bars([100, 100, 100])}, cfg)
    assert res.fills[0].price == pytest.approx(100 * 1.001)
    assert res.fills[0].fee == pytest.approx(0.1)


def test_news_only_visible_after_available_at():
    seen = []

    class Watch(Strategy):
        def on_news(self, ctx, item):
            seen.append((item.symbol, ctx.now))
            return [SimOrder(item.symbol, "buy", 1)]

    data = {"X": bars([100, 100, 100, 100], opens=[100, 101, 102, 103])}
    news = [NewsEvent("X", T0 + DAY + timedelta(hours=2), impact=1.0, event_type="earnings",
                      source="r", sentiment=0.5)]
    res = run(Watch(), data, news=news)
    assert seen == [("X", T0 + DAY + timedelta(hours=2))]
    assert res.fills[0].price == 102  # first open strictly after the news


def test_risk_manager_rejects_oversized_orders():
    class Huge(Strategy):
        def on_bar(self, ctx, symbol, bar):
            return [SimOrder(symbol, "buy", 1000)] if ctx.position(symbol) == 0 else []

    res = run(Huge(), {"X": bars([100] * 5)})
    assert res.fills == []
    assert res.rejections["max_position"] > 0


def test_short_round_trip_pnl():
    class ShortOnce(Strategy):
        def __init__(self):
            self.n = 0

        def on_bar(self, ctx, symbol, bar):
            self.n += 1
            if self.n == 1:
                return [SimOrder(symbol, "short", 5)]
            if self.n == 3:
                return [SimOrder(symbol, "cover", 5)]
            return []

    res = run(ShortOnce(), {"X": bars([100, 100, 90, 90], opens=[100, 100, 95, 90])})
    (t,) = res.trades
    assert t.direction == "short"
    assert t.pnl == pytest.approx(5 * (100 - 90))


def test_multi_symbol_equity_is_marked_to_market():
    class BuyBoth(Strategy):
        def __init__(self):
            self.bought = set()

        def on_bar(self, ctx, symbol, bar):
            if symbol not in self.bought:
                self.bought.add(symbol)
                return [SimOrder(symbol, "buy", 5)]
            return []

    data = {"A": bars([100, 100, 110]), "B": bars([50, 50, 40])}
    res = run(BuyBoth(), data)
    assert res.equity[-1] == pytest.approx(10_000 + 5 * 10 + 5 * -10)
    assert len(res.timestamps) == 3
    assert np.all(np.diff([t.timestamp() for t in res.timestamps]) > 0)
    assert "sharpe" in res.metrics
