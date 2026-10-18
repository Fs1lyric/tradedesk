"""Performance metrics from equity curves, return series and trade P&L."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PERIODS = 252


def returns_from_equity(equity: np.ndarray, flows: np.ndarray | None = None) -> np.ndarray:
    """r_t = E_t / (E_{t-1} + flows_t) - 1, so deposits and withdrawals aren't counted as return."""
    eq = np.asarray(equity, dtype=float)
    fl = np.zeros(len(eq)) if flows is None else np.asarray(flows, dtype=float)
    return eq[1:] / (eq[:-1] + fl[1:]) - 1


def sharpe(r: np.ndarray, rf: float = 0.0, periods: int = PERIODS) -> float:
    r = np.asarray(r, dtype=float) - rf
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(math.sqrt(periods) * r.mean() / r.std(ddof=1))


def sortino(r: np.ndarray, rf: float = 0.0, periods: int = PERIODS) -> float:
    r = np.asarray(r, dtype=float) - rf
    dd = math.sqrt(float(np.mean(np.minimum(r, 0) ** 2))) if len(r) else 0.0
    return 0.0 if dd == 0 else float(math.sqrt(periods) * r.mean() / dd)


def max_drawdown(equity: np.ndarray) -> float:
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return 0.0
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def drawdown_series(equity: np.ndarray) -> np.ndarray:
    eq = np.asarray(equity, dtype=float)
    return eq / np.maximum.accumulate(eq) - 1


def cumulative_return(r: np.ndarray) -> float:
    return float(np.prod(1 + np.asarray(r, dtype=float)) - 1)


def cagr(r: np.ndarray, periods: int = PERIODS) -> float:
    if len(r) == 0:
        return 0.0
    growth = 1 + cumulative_return(r)
    return float(growth ** (periods / len(r)) - 1) if growth > 0 else -1.0


def volatility(r: np.ndarray, periods: int = PERIODS) -> float:
    r = np.asarray(r, dtype=float)
    return float(r.std(ddof=1) * math.sqrt(periods)) if len(r) > 1 else 0.0


@dataclass(frozen=True)
class TradeStats:
    n_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float


def trade_stats(pnl: np.ndarray) -> TradeStats:
    p = np.asarray(pnl, dtype=float)
    if len(p) == 0:
        return TradeStats(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins, losses = p[p > 0], p[p < 0]
    loss_sum = -losses.sum()
    pf = float(wins.sum() / loss_sum) if loss_sum > 0 else math.inf
    return TradeStats(
        n_trades=len(p),
        win_rate=float(len(wins) / len(p)),
        profit_factor=pf,
        expectancy=float(p.mean()),
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(-losses.mean()) if len(losses) else 0.0,
    )


def beta_alpha(r: np.ndarray, bench: np.ndarray, periods: int = PERIODS) -> tuple[float, float]:
    """OLS beta and annualized alpha of r on bench."""
    r, b = np.asarray(r, dtype=float), np.asarray(bench, dtype=float)
    var = b.var(ddof=1)
    if len(r) < 2 or var == 0:
        return 0.0, 0.0
    beta = float(np.cov(r, b, ddof=1)[0, 1] / var)
    return beta, float((r.mean() - beta * b.mean()) * periods)


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


def summarize(
    equity: np.ndarray,
    flows: np.ndarray | None = None,
    trade_pnls: np.ndarray | None = None,
    bench_returns: np.ndarray | None = None,
    rf: float = 0.0,
) -> dict[str, float | int | None]:
    r = returns_from_equity(equity, flows) if len(equity) > 1 else np.array([])
    ts = trade_stats(trade_pnls if trade_pnls is not None else np.array([]))
    out: dict[str, float | int | None] = {
        "cumulative_return": cumulative_return(r),
        "cagr": cagr(r),
        "volatility": volatility(r),
        "sharpe": sharpe(r, rf),
        "sortino": sortino(r, rf),
        "max_drawdown": max_drawdown(np.asarray(equity, dtype=float)),
        "n_trades": ts.n_trades,
        "win_rate": ts.win_rate,
        "profit_factor": _finite(ts.profit_factor),
        "expectancy": ts.expectancy,
        "avg_win": ts.avg_win,
        "avg_loss": ts.avg_loss,
    }
    if bench_returns is not None and len(bench_returns) == len(r):
        out["beta"], out["alpha"] = beta_alpha(r, bench_returns)
    return out
