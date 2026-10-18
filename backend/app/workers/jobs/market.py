"""Price bars in Postgres: sync from the data API, load for signals and backtests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import PriceBar, Rule, WatchlistSymbol
from app.services.brokers.base import PriceBar as Bar


class BarSource(Protocol):
    async def get_bars(self, symbols: list[str], timeframe: str, start: str, end: str
                       ) -> dict[str, list[Bar]]: ...


async def upsert_bars(sessions: async_sessionmaker[AsyncSession], timeframe: str,
                      data: dict[str, list[Bar]]) -> int:
    rows = [dict(symbol=sym, timeframe=timeframe, ts=b.ts, open=b.open, high=b.high, low=b.low,
                 close=b.close, volume=b.volume, vwap=b.vwap)
            for sym, bars in data.items() for b in bars]
    if not rows:
        return 0
    async with sessions.begin() as s:
        for i in range(0, len(rows), 2000):  # stay under the bind-parameter limit
            stmt = insert(PriceBar).values(rows[i:i + 2000])
            await s.execute(stmt.on_conflict_do_update(
                index_elements=["symbol", "timeframe", "ts"],
                set_={c: stmt.excluded[c] for c in
                      ("open", "high", "low", "close", "volume", "vwap")},
            ))
    return len(rows)


async def load_bars(
    sessions: async_sessionmaker[AsyncSession], symbols: Sequence[str], timeframe: str,
    start: datetime | None = None, end: datetime | None = None,
) -> dict[str, list[Bar]]:
    q = select(PriceBar).where(PriceBar.symbol.in_(symbols), PriceBar.timeframe == timeframe)
    if start is not None:
        q = q.where(PriceBar.ts >= start)
    if end is not None:
        q = q.where(PriceBar.ts <= end)
    out: dict[str, list[Bar]] = {s: [] for s in symbols}
    async with sessions() as s:
        for r in await s.scalars(q.order_by(PriceBar.symbol, PriceBar.ts)):
            out[r.symbol].append(Bar(r.ts, r.open, r.high, r.low, r.close, r.volume, r.vwap))
    return out


async def sync_bars(sessions: async_sessionmaker[AsyncSession], source: BarSource,
                    symbols: list[str], timeframe: str, start: datetime, end: datetime) -> int:
    if not symbols:
        return 0
    data = await source.get_bars(symbols, timeframe, start.isoformat(), end.isoformat())
    return await upsert_bars(sessions, timeframe, data)


async def universe(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    """Every symbol anyone watches or trades by rule."""
    async with sessions() as s:
        syms = set(await s.scalars(select(WatchlistSymbol.symbol).distinct()))
        for d in await s.scalars(select(Rule.definition).where(Rule.enabled)):
            syms.update((d.get("universe") or {}).get("symbols") or [])
    return sorted(syms)
