"""Features, composite direction score, return calibration, ranking and position sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from sklearn.isotonic import IsotonicRegression

from app.services.signals import indicators as ind

MIN_BARS = 60

DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.25,
    "momentum": 0.20,
    "sentiment": 0.25,
    "rsi": 0.10,
    "breakout": 0.10,
    "volume": 0.10,
}
FEATURE_NAMES = ["trend", "momentum", "rsi", "breakout", "volume", "sentiment", "sentiment_attn"]


@dataclass(frozen=True)
class Bars:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return len(self.close)


def _last(x: np.ndarray) -> float:
    v = float(x[-1])
    return 0.0 if math.isnan(v) else v


def compute_features(
    bars: Bars, sentiment: float = 0.0, attention_z: float = 0.0
) -> dict[str, float]:
    """Feature values at the last bar. Normalized features are in [-1, 1]."""
    n = len(bars)
    if n < MIN_BARS:
        raise ValueError(f"need at least {MIN_BARS} bars, got {n}")
    c = bars.close
    atr = _last(ind.atr(bars.high, bars.low, c, 14)) or 1e-9
    sma20, sma50 = _last(ind.sma(c, 20)), _last(ind.sma(c, 50))
    sma200 = _last(ind.sma(c, 200)) if n >= 200 else None
    _, _, hist = ind.macd(c)
    rsi = _last(ind.rsi(c, 14)) or 50.0
    adx = _last(ind.adx(bars.high, bars.low, c, 14))
    pb = _last(ind.percent_b(c, 20, 2.0))
    vol_z = ind.zscore(float(bars.volume[-1]), bars.volume[-21:-1])
    log_r = np.diff(np.log(c))
    sigma = _last(ind.ewma_vol(log_r))

    rsi_feature = (50 - rsi) / 50 if adx < 20 else (rsi - 50) / 50
    regime = 0.0 if sma200 is None else (1.0 if c[-1] > sma200 else -1.0)
    return {
        "trend": math.tanh((sma20 - sma50) / atr),
        "momentum": math.tanh(_last(hist) / atr),
        "rsi": float(np.clip(rsi_feature, -1, 1)),
        "breakout": float(np.clip(2 * pb - 1, -1, 1)),
        "volume": math.tanh(vol_z / 2),
        "sentiment": float(np.clip(sentiment, -1, 1)),
        "sentiment_attn": float(np.clip(sentiment * np.clip(attention_z / 3, 0, 1), -1, 1)),
        "regime_long": regime,
        "close": float(c[-1]),
        "atr": atr,
        "sigma_daily": sigma,
        "rsi14": rsi,
        "adx": adx,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200 if sma200 is not None else float("nan"),
        "macd_hist": _last(hist),
        "percent_b": pb,
        "volume_z": vol_z,
    }


def direction_score(features: dict[str, float], weights: dict[str, float] | None = None) -> float:
    w = DEFAULT_WEIGHTS if weights is None else weights
    return float(sum(wk * features.get(k, 0.0) for k, wk in w.items()))


def fit_weights(X: np.ndarray, y: np.ndarray, names: list[str], l2: float = 1.0) -> dict[str, float]:
    """Ridge regression (no intercept) of the vol-scaled forward return on the features."""
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    A = X.T @ X + l2 * np.eye(X.shape[1])
    w = np.linalg.solve(A, X.T @ y)
    return dict(zip(names, map(float, w), strict=True))


@dataclass
class Calibrator:
    """Isotonic map from direction score D to expected forward return, with bucket support."""

    bandwidth: float = 0.1
    _iso: IsotonicRegression | None = None
    _d: np.ndarray = field(default_factory=lambda: np.array([]))

    def fit(self, d: np.ndarray, fwd_returns: np.ndarray) -> Calibrator:
        self._d = np.sort(np.asarray(d, dtype=float))
        self._iso = IsotonicRegression(out_of_bounds="clip", increasing=True)
        self._iso.fit(d, fwd_returns)
        return self

    def expected_return(self, d: float) -> float:
        if self._iso is None:
            raise RuntimeError("calibrator not fitted")
        return float(self._iso.predict([d])[0])

    def support(self, d: float) -> int:
        lo = np.searchsorted(self._d, d - self.bandwidth, "left")
        hi = np.searchsorted(self._d, d + self.bandwidth, "right")
        return int(hi - lo)


def position_size(
    equity: float,
    price: float,
    atr: float,
    risk_per_trade: float = 0.005,
    stop_atr: float = 2.0,
    max_position_pct: float = 0.10,
    kelly_fraction: float | None = None,
) -> int:
    if atr <= 0 or price <= 0:
        return 0
    qty = (equity * risk_per_trade) / (stop_atr * atr)
    qty = min(qty, max_position_pct * equity / price)
    if kelly_fraction is not None:
        qty = min(qty, 0.25 * kelly_fraction * equity / price)
    return max(0, math.floor(qty + 1e-9))


def exits(
    entry: float, atr: float, side: str, stop_atr: float = 2.0, target_atr: float = 3.0
) -> tuple[float, float]:
    """(stop, target) for a new position."""
    if side in ("buy", "long"):
        return entry - stop_atr * atr, entry + target_atr * atr
    return entry + stop_atr * atr, entry - target_atr * atr


@dataclass(frozen=True)
class Candidate:
    symbol: str
    expected_return: float
    sigma_h: float
    cost: float
    support: int
    price: float
    adv_dollars: float
    regime_long: float
    sentiment: float
    earnings_within_days: int | None


@dataclass(frozen=True)
class RankFilters:
    min_price: float = 5.0
    min_adv_dollars: float = 5e6
    min_earnings_days: int = 2
    regime_override_sentiment: float = 0.6
    full_confidence_support: int = 200
    allow_short: bool = True


@dataclass(frozen=True)
class Ranked:
    symbol: str
    side: str
    expected_return: float
    score: float
    confidence: float
    rank_key: float


def rank_candidates(cands: list[Candidate], f: RankFilters) -> list[Ranked]:
    out = []
    for c in cands:
        if c.price < f.min_price or c.adv_dollars < f.min_adv_dollars or c.sigma_h <= 0:
            continue
        if c.earnings_within_days is not None and c.earnings_within_days < f.min_earnings_days:
            continue
        side = "buy" if c.expected_return > 0 else "short"
        if side == "short" and not f.allow_short:
            continue
        if side == "buy" and c.regime_long < 0 and c.sentiment <= f.regime_override_sentiment:
            continue
        if side == "short" and c.regime_long > 0 and c.sentiment >= -f.regime_override_sentiment:
            continue
        score = (abs(c.expected_return) - c.cost) / c.sigma_h
        if score <= 0:
            continue
        conf = min(1.0, c.support / f.full_confidence_support)
        out.append(Ranked(c.symbol, side, c.expected_return, score, conf, score * conf))
    return sorted(out, key=lambda r: -r.rank_key)
