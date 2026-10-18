import numpy as np
import pytest

from app.services.signals.composite import (
    Bars,
    Calibrator,
    Candidate,
    RankFilters,
    compute_features,
    direction_score,
    exits,
    fit_weights,
    position_size,
    rank_candidates,
)


def trending_bars(n=260, slope=0.5, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + slope * np.arange(n) + rng.normal(0, 0.5, n)
    return Bars(
        open=close - 0.1, high=close + 1, low=close - 1, close=close,
        volume=np.full(n, 1_000_000.0),
    )


def test_features_in_range_and_trend_positive_in_uptrend():
    f = compute_features(trending_bars(), sentiment=0.4, attention_z=1.0)
    for k in ["trend", "momentum", "rsi", "breakout", "volume", "sentiment", "sentiment_attn"]:
        assert -1 <= f[k] <= 1, k
    assert f["trend"] > 0.5
    assert f["regime_long"] == 1.0
    assert f["sentiment"] == 0.4
    assert f["atr"] > 0 and f["sigma_daily"] > 0


def test_features_need_enough_history():
    with pytest.raises(ValueError):
        compute_features(trending_bars(n=30))


def test_direction_score_is_weighted_sum():
    f = {"trend": 1.0, "momentum": -1.0, "sentiment": 0.5}
    assert direction_score(f, {"trend": 0.5, "momentum": 0.25, "sentiment": 1.0}) == 0.75


def test_fit_weights_recovers_linear_relation():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(2000, 3))
    y = X @ np.array([0.5, 0.0, -0.3]) + rng.normal(0, 0.01, 2000)
    w = fit_weights(X, y, ["a", "b", "c"], l2=1e-3)
    assert w["a"] == pytest.approx(0.5, abs=0.01)
    assert w["c"] == pytest.approx(-0.3, abs=0.01)


def test_calibrator_is_monotone_and_reports_support():
    rng = np.random.default_rng(4)
    d = rng.uniform(-1, 1, 3000)
    fwd = 0.02 * d + rng.normal(0, 0.005, 3000)
    cal = Calibrator().fit(d, fwd)
    assert cal.expected_return(0.8) > cal.expected_return(0.0) > cal.expected_return(-0.8)
    assert cal.support(0.0) > 100
    assert cal.support(5.0) == 0


def test_position_size_respects_risk_and_caps():
    # risk $500 / (2 * ATR 2.5) = 100 shares; cap at 10% of 100k / $50 = 200
    assert position_size(100_000, 50, atr=2.5) == 100
    # cap binds: tiny ATR
    assert position_size(100_000, 50, atr=0.01) == 200
    assert position_size(100_000, 50, atr=0.01, kelly_fraction=0.1) == 50  # 0.25*0.1*100k/50
    assert position_size(100_000, 50, atr=0) == 0


def test_exits_long_and_short():
    assert exits(100, atr=2, side="buy") == (96, 106)
    assert exits(100, atr=2, side="short") == (104, 94)


def test_rank_filters_and_orders():
    base = dict(price=50, adv_dollars=50e6, sigma_h=0.03, cost=0.001, support=500,
                regime_long=1.0, sentiment=0.0, earnings_within_days=None)
    cands = [
        Candidate(symbol="A", expected_return=0.02, **base),
        Candidate(symbol="B", expected_return=0.04, **base),
        Candidate(symbol="C", expected_return=0.0005, **base),  # below cost
        Candidate(symbol="D", expected_return=0.05, **{**base, "price": 2}),  # penny
        Candidate(symbol="E", expected_return=0.05, **{**base, "regime_long": -1.0}),
        Candidate(symbol="F", expected_return=0.05, **{**base, "earnings_within_days": 1}),
        Candidate(symbol="G", expected_return=-0.04, **{**base, "regime_long": -1.0}),  # short ok
    ]
    ranked = rank_candidates(cands, RankFilters())
    assert [r.symbol for r in ranked] == ["B", "G", "A"]
    assert ranked[1].side == "short"
    b = ranked[0]
    assert b.score == pytest.approx((0.04 - 0.001) / 0.03)
    assert b.confidence == 1.0


def test_feature_frame_is_causal():
    """Features at t from the full series equal features from the series cut at t."""
    from app.services.signals.composite import compute_feature_frame

    full = trending_bars(n=300, seed=9)
    frame = compute_feature_frame(full)
    for t in (80, 150, 299):
        cut = Bars(*(getattr(full, k)[: t + 1] for k in ("open", "high", "low", "close", "volume")))
        part = compute_feature_frame(cut)
        for k, v in frame.items():
            a, b = v[t], part[k][t]
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b), (k, t)
