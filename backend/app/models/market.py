from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class Symbol(Base):
    __tablename__ = "symbols"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    tradable: Mapped[bool] = mapped_column(default=True)
    shortable: Mapped[bool] = mapped_column(default=False)


class PriceBar(Base):
    """OHLCV bars; a Timescale hypertable on ts when the extension exists."""

    __tablename__ = "price_bars"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)  # 1Min | 1Hour | 1Day
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    volume: Mapped[float]
    vwap: Mapped[float | None]


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # noqa: E501
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class WatchlistSymbol(Base):
    __tablename__ = "watchlist_symbols"

    watchlist_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("watchlists.id", ondelete="CASCADE"), primary_key=True)  # noqa: E501
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    added_at: Mapped[datetime] = created_at()
