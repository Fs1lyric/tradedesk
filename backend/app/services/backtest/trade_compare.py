"""Compare each closed trade with simple alternatives over the same symbol and dates."""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app.services.analytics.lots import Trade
from app.services.brokers.base import PriceBar
from app.services.signals.indicators import atr as atr_fn


@dataclass
class TradeComparison:
    symbol: str
    direction: str
    actual_return: float
    mfe: float
    mae: float
    exit_efficiency: float | None
    alternatives: dict[str, float] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    rows: list[TradeComparison]
    mean_diff: dict[str, tuple[float, float, float]]  # alt -> (lo95, mean, hi95) of alt - actual


def _bar_index(rows: list[PriceBar], ts: datetime) -> int:
    return bisect.bisect_right([b.ts for b in rows], ts) - 1


def _atr_exit(rows: list[PriceBar], i: int, entry: float, sign: int, atr: float,
              max_bars: int = 20) -> float | None:
    stop = entry - sign * 2 * atr
    target = entry + sign * 3 * atr
    for j in range(i + 1, min(len(rows), i + 1 + max_bars)):
        b = rows[j]
        hit_stop = b.low <= stop if sign > 0 else b.high >= stop
        hit_target = b.high >= target if sign > 0 else b.low <= target
        if hit_stop:
            return sign * (stop - entry) / entry
        if hit_target:
            return sign * (target - entry) / entry
    last = min(len(rows) - 1, i + max_bars)
    return sign * (rows[last].close - entry) / entry if last > i else None


def compare_trades(
    trades: list[Trade],
    bars: dict[str, list[PriceBar]],
    bench: list[PriceBar],
    holds: Sequence[int] = (1, 5, 20),
    n_boot: int = 1000,
    seed: int = 0,
) -> ComparisonReport:
    rows_out: list[TradeComparison] = []
    bclose = {b.ts.date(): b.close for b in bench}
    for t in trades:
        rows = bars.get(t.symbol)
        if not t.closed or not rows or t.exit_at is None or t.exit_price is None:
            continue
        i = _bar_index(rows, t.entry_at)
        k = _bar_index(rows, t.exit_at)
        if i < 0:
            continue
        sign = 1 if t.direction == "long" else -1
        entry = t.entry_price
        actual = sign * (t.exit_price - entry) / entry
        span = rows[i : max(i, k) + 1]
        best = max(b.high for b in span) if sign > 0 else min(b.low for b in span)
        worst = min(b.low for b in span) if sign > 0 else max(b.high for b in span)
        mfe = sign * (best - entry) / entry
        mae = sign * (worst - entry) / entry
        alts: dict[str, float] = {}
        for h in holds:
            if i + h < len(rows):
                alts[f"hold_{h}"] = sign * (rows[i + h].close - entry) / entry
        high = np.array([b.high for b in rows[: i + 1]])
        low = np.array([b.low for b in rows[: i + 1]])
        close = np.array([b.close for b in rows[: i + 1]])
        a = atr_fn(high, low, close, 14)
        if len(a) and not np.isnan(a[-1]) and a[-1] > 0:
            r = _atr_exit(rows, i, entry, sign, float(a[-1]))
            if r is not None:
                alts["atr_exit"] = r
        b0, b1 = bclose.get(t.entry_at.date()), bclose.get(t.exit_at.date())
        if b0 and b1:
            alts["benchmark"] = b1 / b0 - 1
        eff = actual / mfe if mfe > 0 else None
        rows_out.append(TradeComparison(t.symbol, t.direction, actual, mfe, mae, eff, alts))

    rng = np.random.default_rng(seed)
    mean_diff: dict[str, tuple[float, float, float]] = {}
    names = sorted({k for r in rows_out for k in r.alternatives})
    for name in names:
        d = np.array([r.alternatives[name] - r.actual_return for r in rows_out
                      if name in r.alternatives])
        if len(d) == 0:
            continue
        boots = rng.choice(d, size=(n_boot, len(d)), replace=True).mean(axis=1)
        mean_diff[name] = (float(np.percentile(boots, 2.5)), float(d.mean()),
                           float(np.percentile(boots, 97.5)))
    return ComparisonReport(rows_out, mean_diff)
