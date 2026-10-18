from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.analytics.metrics import sharpe
from app.services.backtest.costs import CostModel
from app.services.backtest.engine import BacktestConfig
from app.services.backtest.walkforward import sample_params, walk_forward
from app.services.brokers.base import PriceBar

T0 = datetime(2022, 1, 3, 5, tzinfo=UTC)


def data(n=500, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for k, sym in enumerate(["A", "B"]):
        c = 100 * np.exp(np.cumsum(rng.normal(0.0008 * (k + 1), 0.012, n)))
        out[sym] = [PriceBar(T0 + timedelta(days=i), c[i - 1] if i else c[i], c[i] * 1.005,
                             c[i] * 0.995, c[i], 2e6) for i in range(n)]
    return out


def test_sample_params_ranges_and_choices():
    space = {"entry_threshold": [0.1, 0.5], "horizon": [3, 10], "allow_short": [True, False, True]}
    rng = np.random.default_rng(0)
    for _ in range(50):
        p = sample_params(space, rng)
        assert 0.1 <= p["entry_threshold"] <= 0.5
        assert isinstance(p["horizon"], int) and 3 <= p["horizon"] <= 10
        assert p["allow_short"] in (True, False)
    p = sample_params({"x": {"choices": [1, 2]}}, rng)
    assert p["x"] in (1, 2)


def test_walk_forward_structure_and_selection_uses_train_only():
    cfg = BacktestConfig(costs=CostModel(slippage_bps=1, spread_bps=1))
    res = walk_forward(
        "composite", {"entry_threshold": [0.05, 0.4], "horizon": [3, 8]}, data(), [], cfg,
        train_bars=200, test_bars=100, n_trials=5, seed=1,
    )
    assert len(res.windows) == 2  # 500 - 200 = 300 bars -> windows at 200 and 300 (400 short)
    assert len(res.oos_returns) == sum(w.test_end - w.test_start for w in res.windows)
    for w in res.windows:
        train_sharpes = [sharpe(r[w.train_start : w.train_end]) for r in res.trial_returns]
        assert w.best_trial == int(np.argmax(train_sharpes))
        np.testing.assert_allclose(
            res.oos_returns[w.oos_slice], res.trial_returns[w.best_trial][w.test_start:w.test_end]
        )
    assert 0.0 <= res.deflated_sharpe <= 1.0
    assert 0.0 <= res.pbo <= 1.0
    assert set(res.oos_metrics) >= {"sharpe", "max_drawdown", "cumulative_return"}
    assert isinstance(res.passes_gate, bool)


def test_walk_forward_needs_enough_data():
    with pytest.raises(ValueError):
        walk_forward("buy_and_hold", {}, data(n=100), [], BacktestConfig(), 200, 50, 2)
