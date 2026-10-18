"""Event-driven bar backtester.

Look-ahead rules:
- A bar becomes visible at its close (bar.ts + bar_duration). Orders placed then fill at the open
  of the first later bar whose start is strictly after the order time.
- News becomes visible at `available_at`, never `published_at`.
- Every order passes through the same RiskManager used live.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from app.services.analytics.lots import Fill, Trade, match_fills
from app.services.analytics.metrics import summarize
from app.services.backtest.costs import CostModel
from app.services.brokers.base import PriceBar
from app.services.risk.manager import (
    SIDE_SIGN,
    AccountState,
    OrderIntent,
    Quote,
    RiskLimits,
    RiskManager,
)


@dataclass
class SimOrder:
    symbol: str
    side: str  # buy | sell | short | cover
    qty: float
    order_type: str = "market"  # market | limit
    limit_price: float | None = None
    stop: float | None = None
    target: float | None = None
    time_stop_bars: int | None = None
    tag: str = ""
    placed_at: datetime | None = None


@dataclass(frozen=True)
class NewsEvent:
    symbol: str
    available_at: datetime
    impact: float
    event_type: str = "other"
    source: str = ""
    sentiment: float = 0.0
    news_id: int | None = None


class Strategy:
    """Override on_bar and/or on_news. `prepare` gets the full data once, for precomputing causal
    features (each value at t must depend only on bars up to t)."""

    def prepare(self, data: dict[str, list[PriceBar]], news: list[NewsEvent]) -> None:
        return None

    def on_bar(self, ctx: Context, symbol: str, bar: PriceBar) -> list[SimOrder]:
        return []

    def on_news(self, ctx: Context, item: NewsEvent) -> list[SimOrder]:
        return []


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    bar_duration: timedelta = timedelta(hours=16)  # Alpaca daily bars start at 00:00 ET
    allow_short: bool = True
    trade_start: datetime | None = None  # orders before this are dropped (indicator warm-up)


@dataclass
class _Bracket:
    stop: float | None
    target: float | None
    time_stop: int | None
    bars_held: int = 0
    exit_queued: bool = False


@dataclass
class BacktestResult:
    timestamps: list[datetime]
    equity: np.ndarray
    fills: list[Fill]
    trades: list[Trade]
    metrics: dict[str, Any]
    rejections: Counter[str]
    n_orders: int

    def daily_returns(self) -> np.ndarray:
        e = self.equity
        return e[1:] / e[:-1] - 1 if len(e) > 1 else np.array([])


class Context:
    def __init__(self, engine: Backtester):
        self._e = engine

    @property
    def now(self) -> datetime:
        return self._e.now

    @property
    def equity(self) -> float:
        return self._e.equity()

    @property
    def cash(self) -> float:
        return self._e.cash

    def position(self, symbol: str) -> float:
        return self._e.qty.get(symbol, 0.0)

    def has_pending(self, symbol: str) -> bool:
        return any(o.symbol == symbol for o in self._e.pending)

    def bar_index(self, symbol: str) -> int:
        """Index of the latest visible bar for symbol, or -1."""
        return self._e.idx.get(symbol, -1)

    def history(self, symbol: str) -> list[PriceBar]:
        i = self.bar_index(symbol)
        return self._e.data[symbol][: i + 1] if i >= 0 else []

    def price(self, symbol: str) -> float | None:
        return self._e.mark.get(symbol)


class Backtester:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.risk = RiskManager(cfg.risk_limits)

    # state helpers
    def equity(self) -> float:
        return self.cash + sum(q * self.mark.get(s, 0.0) for s, q in self.qty.items())

    def _stats(self, symbol: str) -> tuple[float, float]:
        i = self.idx.get(symbol, -1)
        if i < 1:
            return 0.0, 0.0
        return float(self.sigma20[symbol][i]), float(self.adv20[symbol][i])

    def run(
        self,
        strategy: Strategy,
        data: dict[str, list[PriceBar]],
        news: list[NewsEvent] | None = None,
    ) -> BacktestResult:
        news = sorted(news or [], key=lambda n: n.available_at)
        self.data = data
        self.cash = self.cfg.initial_cash
        self.qty: dict[str, float] = {}
        self.mark: dict[str, float] = {}
        self.idx: dict[str, int] = {}
        self.pending: list[SimOrder] = []
        self.brackets: dict[str, _Bracket] = {}
        self.fills: list[Fill] = []
        self.rejections: Counter[str] = Counter()
        self.n_orders = 0
        self._ids = itertools.count(1)
        self._precompute()
        strategy.prepare(data, news)
        ctx = Context(self)

        events: list[tuple[datetime, int, str, int]] = []
        for sym, rows in data.items():
            events.extend((b.ts + self.cfg.bar_duration, 1, sym, i) for i, b in enumerate(rows))
        events.extend((n.available_at, 0, n.symbol, j) for j, n in enumerate(news))
        events.sort(key=lambda e: (e[0], e[1], e[2]))

        timestamps: list[datetime] = []
        equity: list[float] = []
        self.day: date | None = None
        self.sod_equity = self.cash
        self.halted = False
        self.orders_today = 0

        for t, group in itertools.groupby(events, key=lambda e: e[0]):
            self.now = t
            if self.day != t.date():
                self.day = t.date()
                self.sod_equity = self.equity()
                self.halted = False
                self.orders_today = 0
            had_bar = False
            for _, kind, sym, i in group:
                if kind == 0:
                    for o in strategy.on_news(ctx, news[i]):
                        self._submit(o)
                    continue
                had_bar = True
                bar = data[sym][i]
                self._fill_pending(sym, bar)
                self._check_bracket(sym, bar)
                self.mark[sym] = bar.close
                self.idx[sym] = i
                self._advance_time_stop(sym)
                for o in strategy.on_bar(ctx, sym, bar):
                    self._submit(o)
            if had_bar:
                timestamps.append(t)
                equity.append(self.equity())

        trades = match_fills(self.fills)
        eq = np.array(equity)
        closed = np.array([tr.pnl for tr in trades if tr.closed])
        metrics = summarize(eq, trade_pnls=closed) if len(eq) else {}
        return BacktestResult(timestamps, eq, self.fills, trades, metrics, self.rejections,
                              self.n_orders)

    def _precompute(self) -> None:
        self.sigma20: dict[str, np.ndarray] = {}
        self.adv20: dict[str, np.ndarray] = {}
        for sym, rows in self.data.items():
            c = np.array([b.close for b in rows], dtype=float)
            v = np.array([b.volume for b in rows], dtype=float)
            lr = np.zeros(len(c))
            if len(c) > 1:
                lr[1:] = np.diff(np.log(np.where(c > 0, c, np.nan)))
            sig = np.zeros(len(c))
            adv = np.zeros(len(c))
            for i in range(len(c)):
                lo = max(0, i - 19)
                sig[i] = np.nanstd(lr[lo + 1 : i + 1]) if i - lo >= 2 else 0.0
                adv[i] = v[lo : i + 1].mean()
            self.sigma20[sym] = np.nan_to_num(sig)
            self.adv20[sym] = adv

    # orders
    def _submit(self, o: SimOrder) -> None:
        if self.cfg.trade_start is not None and self.now < self.cfg.trade_start:
            return
        price = self.mark.get(o.symbol)
        if price is None:
            self.rejections["no_price"] += 1
            return
        o.placed_at = self.now
        sign = SIDE_SIGN[o.side]
        projected = dict(self.qty)
        for p in self.pending:
            projected[p.symbol] = projected.get(p.symbol, 0.0) + SIDE_SIGN[p.side] * p.qty
        held = projected.get(o.symbol, 0.0)
        if not self.cfg.allow_short and held + sign * o.qty < -1e-9:
            self.rejections["short_disabled"] += 1
            return
        positions = {s: q * self.mark.get(s, 0.0) for s, q in projected.items() if q}
        eq = self.equity()
        gross = sum(abs(v) for v in positions.values())
        state = AccountState(
            equity=eq,
            buying_power=max(0.0, eq * self.cfg.risk_limits.max_gross_exposure - gross),
            start_of_day_equity=self.sod_equity,
            positions=positions,
            market_open=True,
            kill_switch=False,
            halted=self.halted,
            orders_last_minute=0,
            orders_today=self.orders_today,
            day_trades_5d=0,
            tradable={o.symbol},
            shortable={o.symbol},
        )
        intent = OrderIntent(
            key=f"bt{next(self._ids)}",
            symbol=o.symbol,
            side=o.side,
            qty=o.qty,
            limit_price=o.limit_price,
            quote=Quote(price, price, self.now),
        )
        res = self.risk.check(intent, state, self.now)
        if res.halt_account:
            self.halted = True
        if not res.passed:
            for c in res.checks:
                if not c.passed:
                    self.rejections[c.name] += 1
            return
        self.orders_today += 1
        self.n_orders += 1
        self.pending.append(o)

    def _fill_pending(self, sym: str, bar: PriceBar) -> None:
        keep = []
        for o in self.pending:
            if o.symbol != sym or o.placed_at is None or o.placed_at >= bar.ts:
                keep.append(o)
                continue
            sign = SIDE_SIGN[o.side]
            ref = bar.open
            if o.order_type == "limit" and o.limit_price is not None:
                if sign > 0 and bar.low > o.limit_price or sign < 0 and bar.high < o.limit_price:
                    continue  # unfilled day order expires
                ref = min(bar.open, o.limit_price) if sign > 0 else max(bar.open, o.limit_price)
            self._fill(sym, sign, o.qty, ref, bar.ts, o)
        self.pending = keep

    def _fill(
        self, sym: str, sign: int, qty: float, ref: float, at: datetime, o: SimOrder | None
    ) -> None:
        sigma, adv = self._stats(sym)
        px = self.cfg.costs.fill_price(sign, ref, qty, sigma, adv)
        fee = self.cfg.costs.commission(qty)
        held = self.qty.get(sym, 0.0)
        new = held + sign * qty
        self.cash -= sign * qty * px + fee
        self.qty[sym] = 0.0 if abs(new) < 1e-9 else new
        self.fills.append(Fill(f"f{next(self._ids)}", sym, sign * qty, px, at, fee))
        if self.qty[sym] == 0.0:
            self.brackets.pop(sym, None)
        elif held == 0.0 and o is not None and (o.stop or o.target or o.time_stop_bars):
            self.brackets[sym] = _Bracket(o.stop, o.target, o.time_stop_bars)

    def _check_bracket(self, sym: str, bar: PriceBar) -> None:
        b = self.brackets.get(sym)
        q = self.qty.get(sym, 0.0)
        if b is None or q == 0:
            return
        long_ = q > 0
        stop, target = b.stop, b.target
        exit_px: float | None = None
        if long_:
            if stop is not None and bar.open <= stop or target is not None and bar.open >= target:
                exit_px = bar.open
            elif stop is not None and bar.low <= stop:
                exit_px = stop  # worst case when the bar also touches the target
            elif target is not None and bar.high >= target:
                exit_px = target
        else:
            if stop is not None and bar.open >= stop or target is not None and bar.open <= target:
                exit_px = bar.open
            elif stop is not None and bar.high >= stop:
                exit_px = stop
            elif target is not None and bar.low <= target:
                exit_px = target
        if exit_px is not None:
            self.pending = [o for o in self.pending if o.symbol != sym]
            self._fill(sym, -1 if long_ else 1, abs(q), exit_px, bar.ts, None)

    def _advance_time_stop(self, sym: str) -> None:
        b = self.brackets.get(sym)
        if b is None or b.time_stop is None:
            return
        b.bars_held += 1
        if b.bars_held >= b.time_stop and not b.exit_queued:
            q = self.qty.get(sym, 0.0)
            b.exit_queued = True
            self.pending.append(SimOrder(sym, "sell" if q > 0 else "cover", abs(q),
                                         tag="time_stop", placed_at=self.now))
