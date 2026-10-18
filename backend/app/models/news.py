from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, REAL, BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NewsSource(Base):
    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)  # rss | alpaca | extension
    url: Mapped[str | None] = mapped_column(Text)
    credibility: Mapped[float] = mapped_column(default=0.5)
    social: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("news_sources.id", ondelete="SET NULL"))  # noqa: E501
    url: Mapped[str] = mapped_column(Text)
    url_canonical: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None]
    available_at: Mapped[datetime] = mapped_column(index=True)  # first seen; no-lookahead clock
    simhash: Mapped[int] = mapped_column(BigInteger)  # stored as signed 64-bit
    cluster_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    event_type: Mapped[str] = mapped_column(Text)
    event_mult: Mapped[float]
    sentiment: Mapped[float]
    novelty: Mapped[float]
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))


class NewsTicker(Base):
    __tablename__ = "news_tickers"

    news_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True)  # noqa: E501
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    confidence: Mapped[float]
    relevance: Mapped[float]
    novelty: Mapped[float]
    impact: Mapped[float]
