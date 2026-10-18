from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk

_ACCOUNT_FK = "broker_accounts.id"


class TradeIntentRow(Base):
    """Every intent the execution service saw, with its risk verdict. Keys repeat on retries."""

    __tablename__ = "trade_intents"
    __table_args__ = (Index("ix_trade_intents_account_created", "account_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"))  # noqa: E501
    symbol: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    qty: Mapped[float]
    order_type: Mapped[str] = mapped_column(Text)
    limit_price: Mapped[float | None]
    stop_loss: Mapped[float | None]
    take_profit: Mapped[float | None]
    time_in_force: Mapped[str] = mapped_column(Text)
    extended_hours: Mapped[bool]
    origin: Mapped[str] = mapped_column(Text)  # manual | rule | recommendation
    rule_id: Mapped[str | None] = mapped_column(Text)
    recommendation_id: Mapped[str | None] = mapped_column(Text)
    cooldown_minutes: Mapped[int | None]
    risk: Mapped[dict[str, Any]]
    passed: Mapped[bool]
    created_at: Mapped[datetime]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_account_status", "account_id", "status"),)

    id: Mapped[str] = uuid_pk()
    intent_key: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"))  # noqa: E501
    client_order_id: Mapped[str] = mapped_column(Text, unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(Text, unique=True)
    symbol: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    qty: Mapped[float]
    order_type: Mapped[str] = mapped_column(Text)
    order_class: Mapped[str] = mapped_column(Text)
    limit_price: Mapped[float | None]
    stop_loss: Mapped[float | None]
    take_profit: Mapped[float | None]
    time_in_force: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # execution.state.OrderStatus
    filled_qty: Mapped[float] = mapped_column(default=0.0)
    filled_avg_price: Mapped[float | None]
    submitted_at: Mapped[datetime | None] = mapped_column(index=True)
    updated_at: Mapped[datetime | None]


class FillRow(Base):
    __tablename__ = "fills"
    __table_args__ = (Index("ix_fills_account_filled", "account_id", "filled_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("orders.id", ondelete="CASCADE"))  # noqa: E501
    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"))  # noqa: E501
    broker_fill_id: Mapped[str] = mapped_column(Text, unique=True)
    symbol: Mapped[str] = mapped_column(Text)
    qty: Mapped[float]  # signed: + buy, - sell
    price: Mapped[float]
    fee: Mapped[float] = mapped_column(default=0.0)
    filled_at: Mapped[datetime]


class Cooldown(Base):
    __tablename__ = "cooldowns"

    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"), primary_key=True)  # noqa: E501
    rule_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    until: Mapped[datetime]


class TradingHalt(Base):
    """A daily-loss halt for one account on one trading day."""

    __tablename__ = "trading_halts"

    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"), primary_key=True)  # noqa: E501
    day: Mapped[date] = mapped_column(primary_key=True)
    halted_at: Mapped[datetime]


class EquitySnapshot(Base):
    """Account equity over time; a Timescale hypertable on ts when the extension exists."""

    __tablename__ = "equity_snapshots"

    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey(_ACCOUNT_FK, ondelete="CASCADE"), primary_key=True)  # noqa: E501
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    equity: Mapped[float]
    cash: Mapped[float]
    buying_power: Mapped[float]
