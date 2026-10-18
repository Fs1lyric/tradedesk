"""Walk-forward optimization with seeded random search, deflated Sharpe and PBO.

Each sampled configuration runs once over the whole period. Strategies are causal, so a
configuration's returns inside a window only depend on data up to that point. Each window picks
the configuration with the best Sharpe on its train slice and takes that configuration's returns
on the following test slice. Only those test slices form the reported out-of-sample curve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.services.analytics.metrics import sharpe, summarize
from app.services.backtest.engine import BacktestConfig, Backtester, BacktestResult, NewsEvent
from app.services.backtest.overfitting import deflated_sharpe, probability_of_backtest_overfitting
from app.services.backtest.strategies import build_strategy
from app.services.brokers.base import PriceBar

GATE_SHARPE = 0.5
GATE_MAX_DD = -0.20
GATE_TRADES = 100
GATE_DSR = 0.95


def sample_params(space: dict[str, Any], rng: np.random.Generator) -> dict[str, Any]:
    """[lo, hi] of two numbers is a uniform range (integer if both ints); any other list, or
    {"choices": [...]}, is a set of choices."""
    out: dict[str, Any] = {}
    for name, spec in space.items():
        if isinstance(spec, dict):
            choices = spec["choices"]
            out[name] = choices[int(rng.integers(len(choices)))]
        elif (
            isinstance(spec, list)
            and len(spec) == 2
            and all(isinstance(v, int | float) and not isinstance(v, bool) for v in spec)
        ):
            lo, hi = spec
            if isinstance(lo, int) and isinstance(hi, int):
                out[name] = int(rng.integers(lo, hi + 1))
            else:
                out[name] = float(rng.uniform(lo, hi))
        elif isinstance(spec, list):
            out[name] = spec[int(rng.integers(len(spec)))]
        else:
            out[name] = spec
    return out


@dataclass
class Window:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    best_trial: int
    best_params: dict[str, Any]
    train_sharpe: float
    test_sharpe: float
    oos_slice: slice


@dataclass
class WalkForwardResult:
    windows: list[Window]
    params: list[dict[str, Any]]
    trial_returns: list[np.ndarray]
    oos_returns: np.ndarray
    oos_equity: np.ndarray
    oos_metrics: dict[str, Any]
    oos_trades: int
    deflated_sharpe: float
    pbo: float | None
    passes_gate: bool
    timestamps: list[Any] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "windows": [
                {
                    "train": [w.train_start, w.train_end],
                    "test": [w.test_start, w.test_end],
                    "best_params": w.best_params,
                    "train_sharpe": w.train_sharpe,
                    "test_sharpe": w.test_sharpe,
                }
                for w in self.windows
            ],
            "oos_metrics": self.oos_metrics,
            "oos_trades": self.oos_trades,
            "deflated_sharpe": self.deflated_sharpe,
            "pbo": self.pbo,
            "passes_gate": self.passes_gate,
            "n_trials": len(self.params),
        }


def _pbo_splits(t: int) -> int:
    for s in (16, 12, 10, 8, 6, 4, 2):
        if t // s >= 10:
            return s
    return 2


def walk_forward(
    strategy: str,
    param_space: dict[str, Any],
    data: dict[str, list[PriceBar]],
    news: list[NewsEvent],
    cfg: BacktestConfig,
    train_bars: int,
    test_bars: int,
    n_trials: int,
    seed: int = 0,
) -> WalkForwardResult:
    rng = np.random.default_rng(seed)
    params = [sample_params(param_space, rng) for _ in range(max(1, n_trials))]
    runs: list[BacktestResult] = [
        Backtester(cfg).run(build_strategy(strategy, p), data, news) for p in params
    ]
    returns = [r.daily_returns() for r in runs]
    t = min(len(r) for r in returns)
    if t < train_bars + test_bars:
        raise ValueError(f"need {train_bars + test_bars} periods, have {t}")
    returns = [r[:t] for r in returns]
    stamps = runs[0].timestamps[1 : t + 1]

    windows: list[Window] = []
    oos_parts: list[np.ndarray] = []
    oos_trades = 0
    start = 0
    while start + train_bars + test_bars <= t:
        tr0, tr1 = start, start + train_bars
        te0, te1 = tr1, tr1 + test_bars
        train_sharpes = [sharpe(r[tr0:tr1]) for r in returns]
        best = int(np.argmax(train_sharpes))
        offset = sum(len(p) for p in oos_parts)
        oos_parts.append(returns[best][te0:te1])
        lo, hi = stamps[te0], stamps[te1 - 1]
        oos_trades += sum(1 for tr in runs[best].trades if lo <= tr.entry_at <= hi)
        windows.append(Window(tr0, tr1, te0, te1, best, params[best], train_sharpes[best],
                              sharpe(returns[best][te0:te1]), slice(offset, offset + te1 - te0)))
        start += test_bars

    oos = np.concatenate(oos_parts)
    equity = cfg.initial_cash * np.concatenate([[1.0], np.cumprod(1 + oos)])
    metrics = summarize(equity)
    trial_sr = np.array([r.mean() / r.std(ddof=1) if r.std(ddof=1) > 0 else 0.0 for r in returns])
    dsr = deflated_sharpe(oos, n_trials=len(params),
                          trial_sharpe_variance=float(trial_sr.var(ddof=1)) if len(params) > 1
                          else 0.0)
    pbo = (
        probability_of_backtest_overfitting(np.column_stack(returns), n_splits=_pbo_splits(t))
        if len(params) > 1
        else None
    )
    max_dd = metrics["max_drawdown"] or 0.0
    passes = bool(
        (metrics["sharpe"] or 0.0) > GATE_SHARPE
        and max_dd > GATE_MAX_DD
        and oos_trades >= GATE_TRADES
        and dsr > GATE_DSR
    )
    return WalkForwardResult(windows, params, returns, oos, equity, metrics, oos_trades, dsr, pbo,
                             passes, stamps)
