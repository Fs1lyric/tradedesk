"""Runtime settings, read from the environment (or a .env file)."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tradedesk"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str
    master_key: str  # base64 of 32 random bytes; wraps the per-secret data keys
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    totp_issuer: str = "tradedesk"
    # Market data and news come from one Alpaca key pair (paper keys work), separate from the
    # users' trading keys. Without them the bar and Alpaca-news jobs are skipped.
    alpaca_data_key: str | None = None
    alpaca_data_secret: str | None = None
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_len(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("master_key")
    @classmethod
    def _master_key_len(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except binascii.Error as e:
            raise ValueError("MASTER_KEY must be base64") from e
        if len(raw) != 32:
            raise ValueError("MASTER_KEY must decode to 32 bytes")
        return v

    @property
    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.master_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
