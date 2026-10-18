"""Signal snapshots: technical features on the latest bar, decayed news sentiment, and a
calibrated expected return."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import NewsItem, NewsTicker, SignalSnapshot
from app.services.nlp.scoring import DecayItem, decayed_sentiment
from app.services.signals import indicators as ind
from app.services.signals.composite import (
    MIN_BARS,
    Bars,
    Calibrator,
    compute_feature_frame,
    direction_score,
    features_at,
)
from app.workers.jobs.market import load_bars

HORIZON = 5  # bars ahead the expected return is for
MIN_CALIBRATION = 100
HISTORY = timedelta(days=400)
NEWS_DAYS = 30
MAX_STALENESS = timedelta(days=5)  # older last bars mean the data feed stopped


def _clean(v: Any) -> Any:
    v = float(v)
    return v if math.isfinite(v) else None


def _bars(rows: Sequence[Any]) -> Bars:
    return Bars(*(np.array([getattr(b, f) for b in rows], dtype=float)
                  for f in ("open", "high", "low", "close", "volume")))


async def _news(s: AsyncSession, symbol: str, now: datetime) -> tuple[float, float, int]:
    """(sentiment in [-1, 1], attention z-score, count in the last 24h)."""
    rows = (await s.execute(
        select(NewsTicker.impact, NewsItem.available_at)
        .join(NewsItem, NewsItem.id == NewsTicker.news_id)
        .where(NewsTicker.symbol == symbol, NewsItem.available_at <= now,
               NewsItem.available_at > now - timedelta(days=NEWS_DAYS))
    )).all()
    raw = decayed_sentiment([DecayItem(i, at) for i, at in rows], now)
    counts = np.zeros(NEWS_DAYS)
    for _, at in rows:
        counts[min(int((now - at).total_seconds() // 86400), NEWS_DAYS - 1)] += 1
    attention = ind.zscore(float(counts[0]), counts[1:]) if len(rows) else 0.0
    return float(np.tanh(raw)), float(attention), int(counts[0])


async def compute_signals(
    sessions: async_sessionmaker[AsyncSession], symbols: Sequence[str], now: datetime,
    timeframe: str = "1Day",
) -> list[SignalSnapshot]:
    data = await load_bars(sessions, symbols, timeframe, start=now - HISTORY, end=now)
    frames = {sym: compute_feature_frame(_bars(rows)) for sym, rows in data.items()
              if len(rows) >= MIN_BARS and rows[-1].ts >= now - MAX_STALENESS}

    # One calibrator pooled over the universe: score at t against the return t -> t+h.
    d_hist, fwd = [], []
    for frame in frames.values():
        c = frame["close"]
        for t in range(MIN_BARS, len(c) - HORIZON):
            d_hist.append(direction_score(features_at(frame, t)))
            fwd.append(c[t + HORIZON] / c[t] - 1)
    cal = Calibrator().fit(np.array(d_hist), np.array(fwd)) if len(fwd) >= MIN_CALIBRATION \
        else None

    snaps = []
    async with sessions.begin() as s:
        for sym, frame in sorted(frames.items()):
            t = len(frame["close"]) - 1
            sentiment, attention, count = await _news(s, sym, now)
            feats = features_at(frame, t, sentiment, attention)
            d = direction_score(feats)
            rows = data[sym][-20:]
            feats.update(
                news_count_24h=count,
                attention_z=attention,
                adv_dollars=float(np.mean([b.close * b.volume for b in rows])),
            )
            snap = SignalSnapshot(
                symbol=sym, ts=now, features={k: _clean(v) for k, v in feats.items()},
                direction_score=d,
                expected_return=cal.expected_return(d) if cal else 0.0,
                support=cal.support(d) if cal else 0,
            )
            values = {c: getattr(snap, c) for c in
                      ("features", "direction_score", "expected_return", "support")}
            await s.execute(insert(SignalSnapshot).values(symbol=sym, ts=now, **values)
                            .on_conflict_do_update(index_elements=["symbol", "ts"], set_=values))
            snaps.append(snap)
    return snaps


async def latest_snapshots(s: AsyncSession, symbols: Sequence[str], now: datetime,
                           max_age: timedelta = timedelta(days=3)) -> dict[str, SignalSnapshot]:
    rows = await s.scalars(
        select(SignalSnapshot)
        .where(SignalSnapshot.symbol.in_(symbols), SignalSnapshot.ts <= now,
               SignalSnapshot.ts > now - max_age)
        .order_by(SignalSnapshot.symbol, SignalSnapshot.ts.desc())
        .ext(distinct_on(SignalSnapshot.symbol))
    )
    return {r.symbol: r for r in rows}
