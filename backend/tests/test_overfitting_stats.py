import numpy as np
import pytest

from app.services.backtest.overfitting import (
    deflated_sharpe,
    expected_max_sharpe,
    probability_of_backtest_overfitting,
)


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(1, 0.01) == pytest.approx(0.0, abs=1e-9)
    assert expected_max_sharpe(100, 0.01) > expected_max_sharpe(10, 0.01) > 0


def test_dsr_penalizes_many_trials():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 1000)  # per-period Sharpe ~0.1
    one = deflated_sharpe(r, n_trials=1, trial_sharpe_variance=0.0)
    many = deflated_sharpe(r, n_trials=500, trial_sharpe_variance=0.002)
    assert one > 0.95
    assert many < one


def test_dsr_noise_is_not_significant():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, 500)
    assert deflated_sharpe(r, n_trials=50, trial_sharpe_variance=0.003) < 0.9


def test_pbo_high_for_pure_noise_low_for_real_edge():
    rng = np.random.default_rng(2)
    noise = rng.normal(0, 0.01, (800, 20))
    assert probability_of_backtest_overfitting(noise, n_splits=8) > 0.3

    edge = rng.normal(0, 0.01, (800, 20))
    edge[:, 0] += 0.003  # one config truly better everywhere
    assert probability_of_backtest_overfitting(edge, n_splits=8) < 0.1


def test_pbo_validates_input():
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.zeros((100, 1)), n_splits=8)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.zeros((100, 4)), n_splits=7)
