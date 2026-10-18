"""Technical indicators on numpy arrays.

Every function returns an array the same length as its input, with NaN during warm-up.
Smoothed indicators (EMA, RSI, ATR, ADX) seed with a simple mean of the first window and then
recurse, the same way TA-Lib does.
"""

from __future__ import annotations

import numpy as np

Array = np.ndarray


def _as_float(x: Array) -> Array:
    return np.asarray(x, dtype=float)


def sma(x: Array, n: int) -> Array:
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def _seeded_recursive(x: Array, n: int, alpha: float, start: int = 0) -> Array:
    """Mean of x[start:start+n] as the seed, then out_t = alpha*x_t + (1-alpha)*out_{t-1}."""
    out = np.full(len(x), np.nan)
    seed_end = start + n
    if len(x) < seed_end:
        return out
    out[seed_end - 1] = x[start:seed_end].mean()
    for t in range(seed_end, len(x)):
        out[t] = alpha * x[t] + (1 - alpha) * out[t - 1]
    return out


def ema(x: Array, n: int) -> Array:
    x = _as_float(x)
    return _seeded_recursive(x, n, 2.0 / (n + 1))


def _ema_skip_nan(x: Array, n: int) -> Array:
    """EMA of a series that starts with NaN warm-up values."""
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) == 0:
        return out
    first = valid[0]
    out[first:] = ema(x[first:], n)
    return out


def rsi(x: Array, n: int = 14) -> Array:
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    delta = np.diff(x)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    ag = gain[:n].mean()
    al = loss[:n].mean()
    for t in range(n, len(x)):
        if t > n:
            ag = (ag * (n - 1) + gain[t - 1]) / n
            al = (al * (n - 1) + loss[t - 1]) / n
        if al == 0:
            out[t] = 100.0 if ag > 0 else 50.0
        else:
            out[t] = 100.0 - 100.0 / (1.0 + ag / al)
    return out


def macd(
    x: Array, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Array, Array, Array]:
    x = _as_float(x)
    line = ema(x, fast) - ema(x, slow)
    sig = _ema_skip_nan(line, signal)
    return line, sig, line - sig


def true_range(high: Array, low: Array, close: Array) -> Array:
    high, low, close = _as_float(high), _as_float(low), _as_float(close)
    prev = np.roll(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    tr[0] = high[0] - low[0]
    return tr


def _wilder(x: Array, n: int, start: int = 0) -> Array:
    return _seeded_recursive(x, n, 1.0 / n, start)


def atr(high: Array, low: Array, close: Array, n: int = 14) -> Array:
    return _wilder(true_range(high, low, close), n)


def bollinger(x: Array, n: int = 20, k: float = 2.0) -> tuple[Array, Array, Array]:
    x = _as_float(x)
    mid = sma(x, n)
    sd = np.full(len(x), np.nan)
    for t in range(n - 1, len(x)):
        sd[t] = x[t - n + 1 : t + 1].std()
    return mid - k * sd, mid, mid + k * sd


def percent_b(x: Array, n: int = 20, k: float = 2.0) -> Array:
    lower, _, upper = bollinger(x, n, k)
    width = upper - lower
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (_as_float(x) - lower) / width
    out[(width == 0) & ~np.isnan(width)] = 0.5
    return out


def adx(high: Array, low: Array, close: Array, n: int = 14) -> Array:
    high, low = _as_float(high), _as_float(low)
    up = np.diff(high, prepend=np.nan)
    down = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm[0] = minus_dm[0] = 0.0
    tr = true_range(high, low, close)
    tr_s = _wilder(tr, n, start=1)
    plus_s = _wilder(plus_dm, n, start=1)
    minus_s = _wilder(minus_dm, n, start=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100 * plus_s / tr_s
        minus_di = 100 * minus_s / tr_s
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    dx = np.where(np.isfinite(dx), dx, np.nan)
    out = np.full(len(high), np.nan)
    valid = np.flatnonzero(~np.isnan(dx))
    if len(valid) >= n:
        out[valid[0] :] = _wilder(dx[valid[0] :], n)
    return out


def ewma_vol(returns: Array, lam: float = 0.94, warmup: int = 20) -> Array:
    """RiskMetrics EWMA volatility; out[t] uses returns up to and including t."""
    r = _as_float(returns)
    out = np.full(len(r), np.nan)
    if len(r) < warmup:
        return out
    var = float(np.mean(r[:warmup] ** 2))
    out[warmup - 1] = np.sqrt(var)
    for t in range(warmup, len(r)):
        var = lam * var + (1 - lam) * r[t] ** 2
        out[t] = np.sqrt(var)
    return out


def zscore(value: float, history: Array) -> float:
    h = _as_float(history)
    h = h[~np.isnan(h)]
    if len(h) < 2:
        return 0.0
    sd = h.std()
    return 0.0 if sd == 0 else float((value - h.mean()) / sd)
