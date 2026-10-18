from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    features: Mapped[dict[str, Any]]
    direction_score: Mapped[float]
    expected_return: Mapped[float]
    support: Mapped[int]


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[str] = uuid_pk()
    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("broker_accounts.id", ondelete="CASCADE"), index=True)  # noqa: E501
    name: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict[str, Any]]  # services.rules.dsl.Rule as JSON
    mode: Mapped[str] = mapped_column(Text, default="approve")  # manual | approve | auto
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at()


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = uuid_pk()
    account_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("broker_accounts.id", ondelete="CASCADE"), index=True)  # noqa: E501
    rule_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("rules.id", ondelete="SET NULL"))  # noqa: E501
    symbol: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    qty: Mapped[float]
    entry_price: Mapped[float]
    stop_loss: Mapped[float | None]
    take_profit: Mapped[float | None]
    expected_return: Mapped[float]
    score: Mapped[float]
    confidence: Mapped[float]
    rationale: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # pending | approved | dismissed | executed | expired
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime | None]
