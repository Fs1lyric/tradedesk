"""The scored news feed, newest first, with keyset pagination on (available_at, id)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, not_found
from app.api.watchlists import normalize_symbol
from app.models import NewsItem, NewsSource, NewsTicker

router = APIRouter(prefix="/news", tags=["news"])


class TickerOut(BaseModel):
    symbol: str
    confidence: float
    relevance: float
    novelty: float
    impact: float


class NewsOut(BaseModel):
    id: int
    title: str
    url: str
    source: str | None
    published_at: datetime | None
    available_at: datetime
    event_type: str
    sentiment: float
    novelty: float
    cluster_id: int | None
    tickers: list[TickerOut]


class NewsDetail(NewsOut):
    body: str


def _feed() -> Select[NewsItem, str]:  # source is None for orphaned items
    return select(NewsItem, NewsSource.name).outerjoin(
        NewsSource, NewsSource.id == NewsItem.source_id
    )


async def _tickers(session: AsyncSession, ids: list[int]) -> dict[int, list[TickerOut]]:
    rows = await session.scalars(
        select(NewsTicker).where(NewsTicker.news_id.in_(ids))
        .order_by(NewsTicker.impact.desc())
    )
    out: dict[int, list[TickerOut]] = {i: [] for i in ids}
    for t in rows:
        out[t.news_id].append(TickerOut(symbol=t.symbol, confidence=t.confidence,
                                        relevance=t.relevance, novelty=t.novelty,
                                        impact=t.impact))
    return out


def _out(item: NewsItem, source: str | None, tickers: list[TickerOut]) -> NewsOut:
    return NewsOut(
        id=item.id, title=item.title, url=item.url, source=source,
        published_at=item.published_at, available_at=item.available_at,
        event_type=item.event_type, sentiment=item.sentiment, novelty=item.novelty,
        cluster_id=item.cluster_id, tickers=tickers,
    )


@router.get("", response_model=list[NewsOut])
async def feed(
    user: UserDep, session: SessionDep,
    symbol: str | None = None,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NewsOut]:
    q = _feed()
    if symbol is not None:
        q = q.where(NewsItem.id.in_(
            select(NewsTicker.news_id).where(NewsTicker.symbol == normalize_symbol(symbol))
        ))
    if before is not None:
        cursor = select(NewsItem.available_at).where(NewsItem.id == before).scalar_subquery()
        q = q.where(tuple_(NewsItem.available_at, NewsItem.id) < tuple_(cursor, before))
    q = q.order_by(NewsItem.available_at.desc(), NewsItem.id.desc()).limit(limit)
    rows = (await session.execute(q)).all()
    tickers = await _tickers(session, [item.id for item, _ in rows])
    return [_out(item, source, tickers[item.id]) for item, source in rows]


@router.get("/{news_id}", response_model=NewsDetail)
async def get_news(news_id: int, user: UserDep, session: SessionDep) -> NewsDetail:
    row = (await session.execute(_feed().where(NewsItem.id == news_id))).first()
    if row is None:
        raise not_found("news item not found")
    item, source = row
    base = _out(item, source, (await _tickers(session, [item.id]))[item.id])
    return NewsDetail(**base.model_dump(), body=item.body)
