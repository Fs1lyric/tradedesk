from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # noqa: E501
    kind: Mapped[str] = mapped_column(Text)  # backtest | walkforward | event_study | compare
    strategy: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(Text, default="queued")  # queued | running | done | failed
    metrics: Mapped[dict[str, Any] | None]
    result: Mapped[dict[str, Any] | None]  # equity curve, trades, folds
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None]
