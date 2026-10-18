"""Celery tasks: each one is a thin synchronous shell around an async job."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.runtime import Runtime
from app.workers.celery_app import app
from app.workers.jobs import accounts, backtests, market, news, rules, signals

log = logging.getLogger(__name__)

BACKTEST_BUDGET_S = 600  # one task drains the queue for at most this long
NEWS_LOOKBACK = timedelta(minutes=30)


def run[R](job: Callable[[Runtime], Awaitable[R]]) -> R:
    async def main() -> R:
        rt = Runtime.from_settings(get_settings())
        try:
            return await job(rt)
        finally:
            await rt.aclose()

    return asyncio.run(main())


def now() -> datetime:
    return datetime.now(UTC)


@app.task
def ingest_news() -> int:
    async def job(rt: Runtime) -> int:
        ingestor = news.NewsIngestor(rt.sessions)
        async with httpx.AsyncClient(headers={"User-Agent": "tradedesk/0.1"}) as client:
            n = await news.fetch_rss_sources(rt.sessions, client, ingestor, now())
        data = rt.data_client()
        if data is not None:
            try:
                symbols = await market.universe(rt.sessions)
                if symbols:
                    t = now()
                    arts = await data.get_news(symbols, (t - NEWS_LOOKBACK).isoformat(),
                                               t.isoformat())
                    src = await news.source_id(rt.sessions, "alpaca", "alpaca", 0.7)
                    n += len(await ingestor.ingest(news.from_alpaca(arts, 0.7, t), src))
            finally:
                await data.aclose()
        return n

    fresh = run(job)
    if fresh:
        refresh_signals.delay(then_rules=True)
    return fresh


@app.task
def sync_daily_bars(days: int = 5) -> int:
    async def job(rt: Runtime) -> int:
        data = rt.data_client()
        if data is None:
            log.warning("no ALPACA_DATA_KEY; skipping bar sync")
            return 0
        try:
            symbols = sorted({*await market.universe(rt.sessions), backtests.DEFAULT_BENCHMARK})
            return await market.sync_bars(rt.sessions, data, symbols, "1Day",
                                          now() - timedelta(days=days), now())
        finally:
            await data.aclose()

    return run(job)


@app.task
def refresh_signals(then_rules: bool = False) -> int:
    async def job(rt: Runtime) -> int:
        symbols = await market.universe(rt.sessions)
        return len(await signals.compute_signals(rt.sessions, symbols, now()))

    n = run(job)
    if then_rules:
        evaluate_rules.delay()
    return n


@app.task
def evaluate_rules() -> int:
    return len(run(lambda rt: rules.run_rules(rt, now())))


@app.task
def reconcile_orders() -> int:
    return sum(len(v) for v in run(accounts.reconcile_all).values())


@app.task
def snapshot_equity() -> int:
    return run(lambda rt: accounts.snapshot_equity(rt, now()))


@app.task
def expire_recommendations() -> int:
    return run(lambda rt: accounts.expire_recommendations(rt.sessions, now()))


@app.task
def run_backtests() -> int:
    async def job(rt: Runtime) -> int:
        done, deadline = 0, time.monotonic() + BACKTEST_BUDGET_S
        while time.monotonic() < deadline and await backtests.run_next(rt.sessions):
            done += 1
        return done

    return run(job)
