from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.analytics import metrics as m
from app.services.analytics.lots import Fill, match_fills

T0 = datetime(2026, 1, 5, 15, tzinfo=UTC)


def f(qty, price, minutes, fee=0.0, fid=None):
    return Fill(fill_id=fid or f"{minutes}", symbol="AAPL", qty=qty, price=price,
                fee=fee, filled_at=T0 + timedelta(minutes=minutes))


def test_round_trip_long_with_partial_fills_and_fees():
    trades = match_fills([f(10, 100, 0, 1), f(10, 102, 1, 1), f(-20, 110, 5, 2)])
    (t,) = trades
    assert t.direction == "long"
    assert t.qty == 20
    assert t.entry_price == pytest.approx(101)
    assert t.exit_price == pytest.approx(110)
    assert t.fees == pytest.approx(4)
    assert t.pnl == pytest.approx(20 * 9 - 4)
    assert t.closed


def test_short_then_flip_to_long_splits_trades():
    trades = match_fills([f(-5, 50, 0), f(8, 45, 1), f(-3, 47, 2)])
    assert [t.direction for t in trades] == ["short", "long"]
    short, long_ = trades
    assert short.pnl == pytest.approx(5 * 5)
    assert long_.qty == 3 and long_.entry_price == 45 and long_.exit_price == 47
    assert long_.closed


def test_open_trade_has_no_exit():
    (t,) = match_fills([f(5, 10, 0), f(-2, 11, 1)])
    assert not t.closed
    assert t.qty == 5
    assert t.realized_pnl == pytest.approx(2)


def test_returns_adjust_for_flows():
    eq = np.array([100.0, 110.0, 220.0])
    flows = np.array([0.0, 0.0, 100.0])  # deposit 100 on day 2
    r = m.returns_from_equity(eq, flows)
    assert r == pytest.approx([0.10, 220 / 210 - 1])


def test_sharpe_sortino_drawdown_cagr():
    r = np.array([0.01, -0.005, 0.02, -0.01, 0.015])
    assert m.sharpe(r) == pytest.approx(np.sqrt(252) * r.mean() / r.std(ddof=1))
    downside = np.minimum(r, 0)
    assert m.sortino(r) == pytest.approx(
        np.sqrt(252) * r.mean() / np.sqrt(np.mean(downside**2))
    )
    eq = np.array([100.0, 120, 90, 95, 130])
    assert m.max_drawdown(eq) == pytest.approx(90 / 120 - 1)
    assert m.cumulative_return(np.array([0.1, 0.1])) == pytest.approx(0.21)
    assert m.cagr(np.full(252, 0.001)) == pytest.approx(1.001**252 - 1)


def test_trade_stats():
    pnl = np.array([10.0, -5, 20, -5, 0])
    s = m.trade_stats(pnl)
    assert s.win_rate == pytest.approx(2 / 5)
    assert s.profit_factor == pytest.approx(30 / 10)
    assert s.expectancy == pytest.approx(pnl.mean())
    assert m.trade_stats(np.array([])).win_rate == 0.0


def test_beta_alpha():
    rng = np.random.default_rng(1)
    bench = rng.normal(0, 0.01, 500)
    strat = 0.0002 + 1.5 * bench
    beta, alpha = m.beta_alpha(strat, bench)
    assert beta == pytest.approx(1.5)
    assert alpha == pytest.approx(0.0002 * 252)


def test_summary_has_all_keys():
    eq = 100 * np.cumprod(1 + np.random.default_rng(2).normal(0.0005, 0.01, 300))
    s = m.summarize(eq, trade_pnls=np.array([1.0, -1.0, 2.0]))
    for k in ["cumulative_return", "cagr", "sharpe", "sortino", "max_drawdown", "win_rate",
              "profit_factor", "expectancy", "n_trades", "volatility"]:
        assert k in s
