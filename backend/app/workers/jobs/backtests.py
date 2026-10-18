"""Run queued backtest_runs rows: plain backtests, walk-forward, event studies and trade
comparisons. Workers claim rows with SKIP LOCKED so several can run side by side."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BacktestRun, NewsItem, NewsSource, NewsTicker
from app.services.backtest.engine import BacktestConfig, Backtester, NewsEvent
from app.services.backtest.event_study import event_study
from app.services.backtest.strategies import build_strategy
from app.services.backtest.trade_compare import compare_trades
from app.services.backtest.walkforward import walk_forward
from app.services.brokers.base import PriceBar
from app.workers.jobs.market import load_bars

log = logging.getLogger(__name__)

WARMUP = timedelta(days=300)  # enough daily bars for a 200-bar SMA before trading starts
MAX_CURVE_POINTS = 1000
DEFAULT_BENCHMARK = "SPY"


def jsonable(x: Any) -> Any:
    """Numpy, dataclasses, datetimes and non-finite floats into plain JSON values."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple | np.ndarray):
        return [jsonable(v) for v in x]
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return jsonable(dataclasses.asdict(x))
    if isinstance(x, datetime | date):
        return x.isoformat()
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _curve(timestamps: list[datetime], equity: np.ndarray) -> list[list[Any]]:
    step = max(1, math.ceil(len(equity) / MAX_CURVE_POINTS))
    idx = list(range(0, len(equity), step))
    if idx and idx[-1] != len(equity) - 1:
        idx[-1] = len(equity) - 1
    return [[timestamps[i].isoformat(), float(equity[i])] for i in idx]


def _trades(trades: list[Any]) -> list[dict[str, Any]]:
    return [{"symbol": t.symbol, "direction": t.direction, "qty": t.qty,
             "entry_at": t.entry_at, "entry_price": t.entry_price, "exit_at": t.exit_at,
             "exit_price": t.exit_price, "pnl": t.realized_pnl, "return_pct": t.return_pct,
             "closed": t.closed} for t in trades]


def execute(kind: str, strategy: str, params: dict[str, Any], data: dict[str, list[PriceBar]],
            bench: list[PriceBar], news: list[NewsEvent]) -> tuple[dict[str, Any], Any]:
    """CPU-bound: runs on a worker thread. Returns (metrics, result)."""
    start = datetime.fromisoformat(params["start"]).replace(tzinfo=UTC)
    opts = params.get("options") or {}
    cfg = BacktestConfig(trade_start=start)
    if kind == "walkforward":
        wf = walk_forward(
            strategy, opts.get("param_space") or {}, data, news, cfg,
            train_bars=int(opts.get("train_bars", 252)), test_bars=int(opts.get("test_bars", 63)),
            n_trials=int(opts.get("n_trials", 20)), seed=int(opts.get("seed", 0)),
        )
        summary = wf.summary()
        metrics = {**(summary.get("oos_metrics") or {}), "deflated_sharpe": wf.deflated_sharpe,
                   "pbo": wf.pbo, "passes_gate": wf.passes_gate, "oos_trades": wf.oos_trades}
        return metrics, summary
    if kind == "event_study":
        horizons = {str(k): int(v) for k, v in (opts.get("horizons") or {"1d": 1}).items()}
        es = event_study(news, data, bench, horizons,
                         min_abs_impact=float(opts.get("min_abs_impact", 0.0)))
        return {"observations": es.observations}, es
    res = Backtester(cfg).run(build_strategy(strategy, params.get("strategy_params") or {}),
                              data, news)
    result: dict[str, Any] = {
        "equity": _curve(res.timestamps, res.equity), "trades": _trades(res.trades),
        "rejections": dict(res.rejections), "n_orders": res.n_orders,
    }
    if kind == "compare":
        report = compare_trades(res.trades, data, bench)
        result["mean_diff"] = report.mean_diff
        result["comparisons"] = report.rows
    return res.metrics, result


async def load_news_events(sessions: async_sessionmaker[AsyncSession], symbols: list[str],
                           start: datetime, end: datetime) -> list[NewsEvent]:
    async with sessions() as s:
        rows = await s.execute(
            select(NewsTicker.symbol, NewsItem.available_at, NewsTicker.impact,
                   NewsItem.event_type, NewsSource.name, NewsItem.sentiment, NewsItem.id)
            .join(NewsItem, NewsItem.id == NewsTicker.news_id)
            .outerjoin(NewsSource, NewsSource.id == NewsItem.source_id)
            .where(NewsTicker.symbol.in_(symbols), NewsItem.available_at >= start,
                   NewsItem.available_at <= end)
            .order_by(NewsItem.available_at)
        )
        return [NewsEvent(sym, at, imp, ev, src or "", sent, nid)
                for sym, at, imp, ev, src, sent, nid in rows]


async def claim_next(sessions: async_sessionmaker[AsyncSession]) -> str | None:
    async with sessions.begin() as s:
        run_id = await s.scalar(text(
            "UPDATE backtest_runs SET status = 'running' WHERE id = ("
            " SELECT id FROM backtest_runs WHERE status = 'queued'"
            " ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id"
        ))
    return None if run_id is None else str(run_id)


async def run_backtest(sessions: async_sessionmaker[AsyncSession], run_id: str) -> None:
    async with sessions() as s:
        run = await s.get_one(BacktestRun, run_id)
        kind, strategy, params = run.kind, run.strategy, run.params
    try:
        start = datetime.fromisoformat(params["start"]).replace(tzinfo=UTC)
        end = datetime.fromisoformat(params["end"]).replace(tzinfo=UTC)
        symbols = list(params["symbols"])
        bench_sym = (params.get("options") or {}).get("benchmark", DEFAULT_BENCHMARK)
        data = await load_bars(sessions, symbols, "1Day", start - WARMUP, end)
        missing = [sym for sym, rows in data.items() if not rows]
        if missing:
            raise ValueError(f"no price bars for {', '.join(missing)}")
        bench = (await load_bars(sessions, [bench_sym], "1Day", start - WARMUP, end))[bench_sym]
        if kind in ("event_study", "compare") and not bench:
            raise ValueError(f"no price bars for benchmark {bench_sym}")
        news = await load_news_events(sessions, symbols, start - WARMUP, end)
        metrics, result = await asyncio.to_thread(
            execute, kind, strategy, params, data, bench, news
        )
        values: dict[str, Any] = {"status": "done", "metrics": jsonable(metrics),
                                  "result": jsonable(result), "error": None}
    except Exception as e:
        log.exception("backtest %s failed", run_id)
        values = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    async with sessions.begin() as s:
        run = await s.get_one(BacktestRun, run_id)
        for k, v in values.items():
            setattr(run, k, v)
        run.finished_at = datetime.now(UTC)


async def run_next(sessions: async_sessionmaker[AsyncSession]) -> str | None:
    """Claim and run one queued backtest; returns its id, or None if the queue is empty."""
    run_id = await claim_next(sessions)
    if run_id is not None:
        await run_backtest(sessions, run_id)
    return run_id
