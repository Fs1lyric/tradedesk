"""Broker accounts: API keys go in envelope-encrypted and never come back out."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep, UserDep, owned_account
from app.core.security import decrypt_secret, encrypt_secret
from app.models import AuditLog, BrokerAccount
from app.services.risk.manager import RiskLimits

router = APIRouter(prefix="/accounts", tags=["accounts"])


class RiskLimitsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_daily_loss_pct: PositiveFloat | None = None
    max_position_pct: PositiveFloat | None = None
    max_gross_exposure: PositiveFloat | None = None
    max_orders_per_minute: PositiveInt | None = None
    max_orders_per_day: PositiveInt | None = None
    max_quote_age_s: PositiveFloat | None = None
    max_limit_deviation: PositiveFloat | None = None
    risk_per_trade: PositiveFloat | None = None


class AccountIn(BaseModel):
    broker: Literal["alpaca"]
    mode: Literal["paper", "live"] = "paper"
    label: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    risk_limits: RiskLimitsPatch = RiskLimitsPatch()


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    kill_switch: bool | None = None
    risk_limits: RiskLimitsPatch | None = None


class AccountOut(BaseModel):
    id: str
    broker: str
    mode: str
    label: str
    api_key_hint: str
    risk_limits: dict[str, Any]
    kill_switch: bool
    created_at: datetime


def merge_limits(current: dict[str, Any], patch: RiskLimitsPatch) -> dict[str, Any]:
    merged = {**RiskLimits.from_dict(current).to_dict(), **patch.model_dump(exclude_none=True)}
    return RiskLimits.from_dict(merged).to_dict()


def _out(acct: BrokerAccount, master_key: bytes) -> AccountOut:
    key = decrypt_secret(acct.api_key_enc, master_key)
    return AccountOut(
        id=acct.id, broker=acct.broker, mode=acct.mode, label=acct.label,
        api_key_hint="…" + key[-4:], risk_limits=acct.risk_limits,
        kill_switch=acct.kill_switch, created_at=acct.created_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccountOut)
async def create_account(
    body: AccountIn, user: UserDep, session: SessionDep, settings: SettingsDep
) -> AccountOut:
    if body.mode == "live" and not user.live_trading_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "live trading is not enabled")
    key = settings.master_key_bytes
    acct = BrokerAccount(
        user_id=user.id, broker=body.broker, mode=body.mode, label=body.label,
        api_key_enc=encrypt_secret(body.api_key, key),
        api_secret_enc=encrypt_secret(body.api_secret, key),
        risk_limits=merge_limits({}, body.risk_limits), kill_switch=False,
    )
    session.add(acct)
    await session.flush()
    await session.refresh(acct)
    session.add(AuditLog(user_id=user.id, action="account_created",
                         detail={"account_id": acct.id, "mode": acct.mode}))
    return _out(acct, key)


@router.get("", response_model=list[AccountOut])
async def list_accounts(user: UserDep, session: SessionDep, settings: SettingsDep
                        ) -> list[AccountOut]:
    rows = await session.scalars(
        select(BrokerAccount).where(BrokerAccount.user_id == user.id)
        .order_by(BrokerAccount.created_at)
    )
    return [_out(a, settings.master_key_bytes) for a in rows]


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(
    account_id: str, user: UserDep, session: SessionDep, settings: SettingsDep
) -> AccountOut:
    return _out(await owned_account(session, user, account_id), settings.master_key_bytes)


@router.patch("/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: str, body: AccountPatch, user: UserDep, session: SessionDep,
    settings: SettingsDep,
) -> AccountOut:
    acct = await owned_account(session, user, account_id)
    if body.label is not None:
        acct.label = body.label
    if body.risk_limits is not None:
        acct.risk_limits = merge_limits(acct.risk_limits, body.risk_limits)
    if body.kill_switch is not None and body.kill_switch != acct.kill_switch:
        acct.kill_switch = body.kill_switch
        session.add(AuditLog(user_id=user.id, action="kill_switch",
                             detail={"account_id": acct.id, "on": body.kill_switch}))
    return _out(acct, settings.master_key_bytes)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: str, user: UserDep, session: SessionDep) -> None:
    acct = await owned_account(session, user, account_id)
    await session.delete(acct)
    session.add(AuditLog(user_id=user.id, action="account_deleted",
                         detail={"account_id": account_id}))
