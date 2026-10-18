import numpy as np
import pytest

from app.services.signals import indicators as ind


def test_sma_matches_hand_computation():
    out = ind.sma(np.array([1.0, 2, 3, 4, 5]), 3)
    assert np.isnan(out[:2]).all()
    assert out[2:] == pytest.approx([2.0, 3.0, 4.0])


def test_ema_seeds_with_sma_then_recurses():
    x = np.array([1.0, 2, 3, 4, 5])
    out = ind.ema(x, 3)
    # seed = mean(1,2,3) = 2; alpha = 0.5
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
    assert out[4] == pytest.approx(0.5 * 5 + 0.5 * 3.0)


def test_rsi_is_100_for_strictly_rising_and_0_for_falling():
    up = np.arange(1.0, 40.0)
    assert ind.rsi(up, 14)[-1] == pytest.approx(100.0)
    assert ind.rsi(up[::-1].copy(), 14)[-1] == pytest.approx(0.0)


def test_rsi_wilder_hand_example():
    # deltas: +1, -1, +2, -1 ; n = 2
    x = np.array([10.0, 11, 10, 12, 11])
    out = ind.rsi(x, 2)
    # seed at index 2: avg_gain = (1+0)/2 = .5, avg_loss = (0+1)/2 = .5 -> 50
    assert out[2] == pytest.approx(50.0)
    # index 3: gain 2 -> ag = (.5*1 + 2)/2 = 1.25, al = (.5*1 + 0)/2 = .25 -> rs 5
    assert out[3] == pytest.approx(100 - 100 / 6)
    assert np.isnan(out[:2]).all()


def test_macd_histogram_is_macd_minus_signal():
    x = np.cumsum(np.random.default_rng(0).normal(size=200)) + 100
    macd, signal, hist = ind.macd(x)
    valid = ~np.isnan(hist)
    assert valid.sum() > 100
    assert hist[valid] == pytest.approx((macd - signal)[valid])


def test_atr_constant_range_bars():
    n = 30
    close = np.full(n, 100.0)
    high = close + 1
    low = close - 1
    out = ind.atr(high, low, close, 14)
    assert out[-1] == pytest.approx(2.0)
    assert np.isnan(out[:13]).all()


def test_percent_b_bounds():
    x = np.array([1.0] * 19 + [2.0])
    pb = ind.percent_b(x, 20, 2.0)
    assert pb[-1] > 1.0  # the jump closes above the upper band
    flat = ind.percent_b(np.full(25, 5.0), 20, 2.0)
    assert flat[-1] == pytest.approx(0.5)  # zero-width bands defined as midpoint


def test_adx_high_in_strong_trend():
    n = 100
    close = np.linspace(100, 200, n)
    high = close + 0.5
    low = close - 0.5
    out = ind.adx(high, low, close, 14)
    assert out[-1] > 50


def test_ewma_vol_constant_returns():
    r = np.full(300, 0.01)
    out = ind.ewma_vol(r, lam=0.94)
    assert out[-1] == pytest.approx(0.01, rel=1e-3)


def test_zscore_last():
    hist = np.array([1.0, 2, 3, 4, 5])
    assert ind.zscore(6.0, hist) == pytest.approx((6 - 3) / np.std(hist))
    assert ind.zscore(1.0, np.array([1.0, 1.0])) == 0.0
