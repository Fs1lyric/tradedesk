"""Strategy families for backtests and walk-forward search.

All of them share the live sizing and exit helpers, and read features from causal frames.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from app.services.backtest.engine import Context, NewsEvent, SimOrder, Strategy
from app.services.brokers.base import PriceBar
from app.services.signals.composite import (
    DEFAULT_WEIGHTS,
    MIN_BARS,
    Bars,
    compute_feature_frame,
    direction_score,
    exits,
    features_at,
    position_size,
)
from app.services.signals.indicators import zscore


class SentimentTracker:
    """Incremental exponentially decayed news sentiment per symbol, with z-score history."""

    def __init__(self, half_life_h: float = 6.0, history: int = 60):
        self.lam = math.log(2) / half_life_h
        self.state: dict[str, tuple[float, datetime]] = {}
        self.hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history))
        self.count_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history))
        self.times: dict[str, deque[datetime]] = defaultdict(deque)

    def _decayed(self, symbol: str, t: datetime) -> float:
        if symbol not in self.state:
            return 0.0
        s, last = self.state[symbol]
        return s * math.exp(-self.lam * max(0.0, (t - last).total_seconds() / 3600))

    def add(self, symbol: str, t: datetime, impact: float) -> None:
        self.state[symbol] = (self._decayed(symbol, t) + impact, t)
        self.times[symbol].append(t)

    def value(self, symbol: str, t: datetime) -> float:
        return self._decayed(symbol, t)

    def _count_24h(self, symbol: str, t: datetime) -> int:
        q = self.times[symbol]
        while q and q[0] < t - timedelta(hours=24):
            q.popleft()
        return len(q)

    def sample(self, symbol: str, t: datetime) -> None:
        self.hist[symbol].append(self.value(symbol, t))
        self.count_hist[symbol].append(float(self._count_24h(symbol, t)))

    def features(self, symbol: str, t: datetime) -> tuple[float, float]:
        """(clip(z, -3, 3) / 3, attention z) at t."""
        z = zscore(self.value(symbol, t), np.array(self.hist[symbol]))
        attn = zscore(float(self._count_24h(symbol, t)), np.array(self.count_hist[symbol]))
        return float(np.clip(z, -3, 3) / 3), attn


def _frames(data: dict[str, list[PriceBar]]) -> dict[str, dict[str, np.ndarray]]:
    out = {}
    for sym, rows in data.items():
        if not rows:
            continue
        bars = Bars(*(np.array([getattr(b, k) for b in rows], dtype=float)
                      for k in ("open", "high", "low", "close", "volume")))
        out[sym] = compute_feature_frame(bars)
    return out


class CompositeStrategy(Strategy):
    def __init__(
        self,
        entry_threshold: float = 0.3,
        weights: dict[str, float] | None = None,
        stop_atr: float = 2.0,
        target_atr: float = 3.0,
        horizon: int = 5,
        risk_per_trade: float = 0.005,
        allow_short: bool = True,
        half_life_h: float = 6.0,
        regime_override: float = 0.6,
    ):
        self.entry_threshold = entry_threshold
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.stop_atr = stop_atr
        self.target_atr = target_atr
        self.horizon = horizon
        self.risk_per_trade = risk_per_trade
        self.allow_short = allow_short
        self.regime_override = regime_override
        self.sent = SentimentTracker(half_life_h)

    def prepare(self, data: dict[str, list[PriceBar]], news: list[NewsEvent]) -> None:
        self.frames = _frames(data)

    def on_news(self, ctx: Context, item: NewsEvent) -> list[SimOrder]:
        self.sent.add(item.symbol, item.available_at, item.impact)
        return []

    def on_bar(self, ctx: Context, symbol: str, bar: PriceBar) -> list[SimOrder]:
        i = ctx.bar_index(symbol)
        sentiment, attn = self.sent.features(symbol, ctx.now)
        self.sent.sample(symbol, ctx.now)
        if i < MIN_BARS or ctx.position(symbol) != 0 or ctx.has_pending(symbol):
            return []
        f = features_at(self.frames[symbol], i, sentiment, attn)
        d = direction_score(f, self.weights)
        if d > self.entry_threshold and (f["regime_long"] >= 0 or sentiment > self.regime_override):
            side = "buy"
        elif (
            self.allow_short
            and d < -self.entry_threshold
            and (f["regime_long"] <= 0 or sentiment < -self.regime_override)
        ):
            side = "short"
        else:
            return []
        qty = position_size(ctx.equity, f["close"], f["atr"], self.risk_per_trade, self.stop_atr)
        if qty <= 0:
            return []
        stop, target = exits(f["close"], f["atr"], side, self.stop_atr, self.target_atr)
        return [SimOrder(symbol, side, qty, stop=stop, target=target,
                         time_stop_bars=self.horizon, tag=f"D={d:.2f}")]


class NewsMomentumStrategy(Strategy):
    direction = 1

    def __init__(
        self,
        threshold: float = 0.3,
        hold: int = 5,
        stop_atr: float = 2.0,
        target_atr: float = 3.0,
        risk_per_trade: float = 0.005,
        event_types: list[str] | None = None,
    ):
        self.threshold = threshold
        self.hold = hold
        self.stop_atr = stop_atr
        self.target_atr = target_atr
        self.risk_per_trade = risk_per_trade
        self.event_types = set(event_types) if event_types else None

    def prepare(self, data: dict[str, list[PriceBar]], news: list[NewsEvent]) -> None:
        self.frames = _frames(data)

    def on_news(self, ctx: Context, item: NewsEvent) -> list[SimOrder]:
        if abs(item.impact) < self.threshold:
            return []
        if self.event_types and item.event_type not in self.event_types:
            return []
        sym = item.symbol
        i = ctx.bar_index(sym)
        if i < 20 or sym not in self.frames or ctx.position(sym) != 0 or ctx.has_pending(sym):
            return []
        atr = float(self.frames[sym]["atr"][i])
        price = ctx.price(sym) or 0.0
        side = "buy" if np.sign(item.impact) * self.direction > 0 else "short"
        qty = position_size(ctx.equity, price, atr, self.risk_per_trade, self.stop_atr)
        if qty <= 0:
            return []
        stop, target = exits(price, atr, side, self.stop_atr, self.target_atr)
        return [SimOrder(sym, side, qty, stop=stop, target=target, time_stop_bars=self.hold,
                         tag=item.event_type)]


class NewsFadeStrategy(NewsMomentumStrategy):
    direction = -1


class TechnicalTrendStrategy(Strategy):
    def __init__(self, fast: int = 20, slow: int = 50, weight: float = 0.09):
        self.fast, self.slow, self.weight = fast, slow, weight

    def prepare(self, data: dict[str, list[PriceBar]], news: list[NewsEvent]) -> None:
        from app.services.signals.indicators import sma

        self.f: dict[str, np.ndarray] = {}
        self.s: dict[str, np.ndarray] = {}
        for sym, rows in data.items():
            c = np.array([b.close for b in rows])
            self.f[sym], self.s[sym] = sma(c, self.fast), sma(c, self.slow)

    def on_bar(self, ctx: Context, symbol: str, bar: PriceBar) -> list[SimOrder]:
        i = ctx.bar_index(symbol)
        f, s = self.f[symbol][i], self.s[symbol][i]
        if np.isnan(s) or ctx.has_pending(symbol):
            return []
        pos = ctx.position(symbol)
        if pos == 0 and f > s:
            qty = math.floor(self.weight * ctx.equity / bar.close)
            return [SimOrder(symbol, "buy", qty)] if qty > 0 else []
        if pos > 0 and f < s:
            return [SimOrder(symbol, "sell", pos)]
        return []


class BuyAndHoldStrategy(Strategy):
    def __init__(self, weight: float | None = None):
        self.weight = weight
        self.bought: set[str] = set()

    def prepare(self, data: dict[str, list[PriceBar]], news: list[NewsEvent]) -> None:
        self.n = max(1, len(data))

    def on_bar(self, ctx: Context, symbol: str, bar: PriceBar) -> list[SimOrder]:
        if symbol in self.bought:
            return []
        self.bought.add(symbol)
        w = self.weight if self.weight is not None else min(0.099, 0.99 / self.n)
        qty = math.floor(w * ctx.equity / bar.close)
        return [SimOrder(symbol, "buy", qty)] if qty > 0 else []


STRATEGIES: dict[str, Callable[..., Strategy]] = {
    "composite": CompositeStrategy,
    "news_momentum": NewsMomentumStrategy,
    "news_fade": NewsFadeStrategy,
    "technical_trend": TechnicalTrendStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
}


def build_strategy(name: str, params: dict[str, Any]) -> Strategy:
    return STRATEGIES[name](**params)
