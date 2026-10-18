"""News event study: abnormal returns after scored news, grouped and tested for significance.

AR_h = r_stock(base, base+h) - beta * r_bench(base, base+h), where `base` is the last bar whose
close was visible when the news became available, and beta is a 60-bar OLS beta before base.
Significance uses standard errors clustered by date, since same-day events are correlated.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from app.services.backtest.engine import NewsEvent
from app.services.brokers.base import PriceBar

BETA_WINDOW = 60
PATTERN_MIN_N = 50
PATTERN_MIN_T = 2.5


def clustered_t(x: np.ndarray, clusters: Sequence[Hashable]) -> float:
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 0.0
    e = x - x.mean()
    sums: dict[Hashable, float] = defaultdict(float)
    for v, c in zip(e, clusters, strict=True):
        sums[c] += v
    g = len(sums)
    if g < 2:
        return 0.0
    var = g / (g - 1) * sum(s * s for s in sums.values()) / n**2
    return float(x.mean() / math.sqrt(var)) if var > 0 else 0.0


def sentiment_bucket(s: float) -> str:
    return "pos" if s > 0.2 else "neg" if s < -0.2 else "neu"


@dataclass(frozen=True)
class GroupStat:
    event_type: str
    source: str
    sentiment_bucket: str
    horizon: str
    n: int
    mean_car: float
    t_stat: float
    hit_rate: float


@dataclass
class EventStudyResult:
    groups: list[GroupStat]
    patterns: list[GroupStat]
    source_stats: dict[str, tuple[float, int]] = field(default_factory=dict)
    observations: int = 0


def _beta(stock: np.ndarray, bench: np.ndarray) -> float:
    if len(stock) < 10 or bench.var() == 0:
        return 1.0
    return float(np.cov(stock, bench, ddof=1)[0, 1] / bench.var(ddof=1))


def event_study(
    events: list[NewsEvent],
    prices: dict[str, list[PriceBar]],
    bench: list[PriceBar],
    horizons: dict[str, int],
    bar_duration: timedelta = timedelta(hours=16),
    min_abs_impact: float = 0.0,
) -> EventStudyResult:
    bench_close = {b.ts: b.close for b in bench}
    obs: list[tuple[NewsEvent, str, float]] = []  # event, horizon, AR

    for sym, rows in prices.items():
        rows = [b for b in rows if b.ts in bench_close]
        if len(rows) < BETA_WINDOW + 2:
            continue
        close = np.array([b.close for b in rows])
        bclose = np.array([bench_close[b.ts] for b in rows])
        r = close[1:] / close[:-1] - 1
        rb = bclose[1:] / bclose[:-1] - 1
        visible = [b.ts + bar_duration for b in rows]
        for ev in events:
            if ev.symbol != sym or abs(ev.impact) < min_abs_impact:
                continue
            base = bisect.bisect_right(visible, ev.available_at) - 1
            if base < BETA_WINDOW:
                continue
            beta = _beta(r[base - BETA_WINDOW : base], rb[base - BETA_WINDOW : base])
            for name, h in horizons.items():
                if base + h >= len(rows):
                    continue
                rs = close[base + h] / close[base] - 1
                rbh = bclose[base + h] / bclose[base] - 1
                obs.append((ev, name, rs - beta * rbh))

    keyed: dict[tuple[str, str, str, str], list[tuple[NewsEvent, float]]] = defaultdict(list)
    for ev, hz, ar in obs:
        b = sentiment_bucket(ev.sentiment)
        for key in ((ev.event_type, "*", "*", hz), (ev.event_type, ev.source, b, hz),
                    ("*", ev.source, "*", hz), ("*", "*", b, hz)):
            keyed[key].append((ev, ar))

    groups = []
    for (et, src, bucket, hz), rows_ in sorted(keyed.items()):
        ars = np.array([a for _, a in rows_])
        signs = np.array([np.sign(e.sentiment or e.impact) for e, _ in rows_])
        hits = float(np.mean(np.sign(ars) == signs)) if len(ars) else 0.0
        groups.append(GroupStat(
            et, src, bucket, hz, len(ars), float(ars.mean()),
            clustered_t(ars, [e.available_at.date() for e, _ in rows_]), hits,
        ))
    patterns = [g for g in groups if g.n >= PATTERN_MIN_N and abs(g.t_stat) > PATTERN_MIN_T]

    first_h = next(iter(horizons), None)
    src_stats = {
        g.source: (g.hit_rate, g.n)
        for g in groups
        if g.event_type == "*" and g.sentiment_bucket == "*" and g.horizon == first_h
    }
    return EventStudyResult(groups, patterns, src_stats, len(obs))


def source_credibility(stats: dict[str, tuple[float, int]], min_n: int = 30) -> dict[str, float]:
    """Map each source's hit rate to a credibility weight in [0.1, 1]."""
    return {
        src: float(np.clip(0.5 + (hit - 0.5) * 2.5, 0.1, 1.0))
        for src, (hit, n) in stats.items()
        if n >= min_n
    }


def lead_lag(sentiment: np.ndarray, returns: np.ndarray, max_lag: int = 24) -> dict[int, float]:
    """corr(sentiment_t, return_{t+lag}) for lag 0..max_lag."""
    s, r = np.asarray(sentiment, dtype=float), np.asarray(returns, dtype=float)
    out = {}
    for lag in range(max_lag + 1):
        a, b = (s, r) if lag == 0 else (s[:-lag], r[lag:])
        if len(a) < 3 or a.std() == 0 or b.std() == 0:
            out[lag] = 0.0
        else:
            out[lag] = float(np.corrcoef(a, b)[0, 1])
    return out
