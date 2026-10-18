from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    live_trading_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at()


class RefreshToken(Base):
    """Issued refresh tokens by jti, so they can be rotated and revoked."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # noqa: E501
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # noqa: E501
    broker: Mapped[str] = mapped_column(Text)  # alpaca | ibkr | schwab
    mode: Mapped[str] = mapped_column(Text, default="paper")  # paper | live
    label: Mapped[str] = mapped_column(Text)
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    risk_limits: Mapped[dict[str, Any]] = mapped_column(default=dict)
    kill_switch: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), index=True)  # noqa: E501
    action: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[datetime] = created_at()
