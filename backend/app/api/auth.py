"""Register, log in, rotate refresh tokens, and enrol TOTP."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep, UserDep, unauthorized
from app.core.config import Settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.models import AuditLog, BrokerAccount, RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            raise ValueError("invalid email")
        return v


class Login(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshBody(BaseModel):
    refresh_token: str


class Me(BaseModel):
    id: str
    email: str
    totp_enabled: bool
    live_trading_enabled: bool


class TotpEnrollment(BaseModel):
    secret: str
    uri: str


class TotpConfirm(BaseModel):
    secret: str
    code: str


async def _issue(session: AsyncSession, user_id: str, settings: Settings) -> TokenPair:
    refresh = create_refresh_token(user_id, settings)
    claims = decode_token(refresh, settings, "refresh")
    session.add(RefreshToken(
        jti=claims["jti"], user_id=user_id,
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    ))
    return TokenPair(access_token=create_access_token(user_id, settings), refresh_token=refresh)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=Me)
async def register(body: Credentials, session: SessionDep) -> Me:
    user = User(email=body.email, password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from e
    session.add(AuditLog(user_id=user.id, action="register"))
    return Me(id=user.id, email=user.email, totp_enabled=False, live_trading_enabled=False)


@router.post("/login", response_model=TokenPair)
async def login(body: Login, session: SessionDep, settings: SettingsDep) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(user.password_hash, body.password):
        raise unauthorized("bad credentials")
    if user.totp_secret_enc is not None:
        if body.totp_code is None:
            raise unauthorized("totp required")
        secret = decrypt_secret(user.totp_secret_enc, settings.master_key_bytes)
        if not verify_totp(secret, body.totp_code):
            raise unauthorized("bad totp code")
    session.add(AuditLog(user_id=user.id, action="login"))
    return await _issue(session, user.id, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshBody, session: SessionDep, settings: SettingsDep
) -> TokenPair | JSONResponse:
    try:
        claims = decode_token(body.refresh_token, settings, "refresh")
    except TokenError as e:
        raise unauthorized("invalid token") from e
    row = await session.get(RefreshToken, claims["jti"], with_for_update=True)
    if row is None:
        raise unauthorized("invalid token")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        # A spent token came back: assume it was stolen and end every session for the user.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        session.add(AuditLog(user_id=row.user_id, action="refresh_reuse",
                             detail={"jti": row.jti}))
        # Returned, not raised: raising would roll the revocation back.
        exc = unauthorized("invalid token")
        return JSONResponse({"detail": exc.detail}, exc.status_code, exc.headers)
    row.revoked_at = now
    return await _issue(session, row.user_id, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshBody, session: SessionDep, settings: SettingsDep) -> None:
    try:
        claims = decode_token(body.refresh_token, settings, "refresh")
    except TokenError:
        return
    row = await session.get(RefreshToken, claims["jti"])
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


@router.get("/me", response_model=Me)
async def me(user: UserDep) -> Me:
    return Me(id=user.id, email=user.email, totp_enabled=user.totp_secret_enc is not None,
              live_trading_enabled=user.live_trading_enabled)


@router.post("/totp/enroll", response_model=TotpEnrollment)
async def totp_enroll(user: UserDep, settings: SettingsDep) -> TotpEnrollment:
    """Hand out a fresh secret. Nothing is stored until a code for it is confirmed, so an
    abandoned enrolment can't lock the user out."""
    if user.totp_secret_enc is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "totp already enabled")
    secret = new_totp_secret()
    return TotpEnrollment(secret=secret, uri=totp_uri(secret, user.email, settings))


@router.post("/totp/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def totp_confirm(
    body: TotpConfirm, user: UserDep, session: SessionDep, settings: SettingsDep
) -> None:
    if user.totp_secret_enc is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "totp already enabled")
    if not verify_totp(body.secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad totp code")
    user.totp_secret_enc = encrypt_secret(body.secret, settings.master_key_bytes)
    session.add(AuditLog(user_id=user.id, action="totp_enabled"))


class LiveEnable(BaseModel):
    code: str


@router.post("/live/enable", response_model=Me)
async def live_enable(
    body: LiveEnable, user: UserDep, session: SessionDep, settings: SettingsDep
) -> Me:
    """Live trading needs TOTP on the account and a fresh code at the moment it is switched on."""
    if user.totp_secret_enc is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enable totp first")
    secret = decrypt_secret(user.totp_secret_enc, settings.master_key_bytes)
    if not verify_totp(secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad totp code")
    user.live_trading_enabled = True
    session.add(AuditLog(user_id=user.id, action="live_trading_enabled"))
    return await me(user)


@router.post("/live/disable", response_model=Me)
async def live_disable(user: UserDep, session: SessionDep) -> Me:
    """Turning live off needs no code, and trips the kill switch on every live account."""
    user.live_trading_enabled = False
    await session.execute(
        update(BrokerAccount)
        .where(BrokerAccount.user_id == user.id, BrokerAccount.mode == "live")
        .values(kill_switch=True)
    )
    session.add(AuditLog(user_id=user.id, action="live_trading_disabled"))
    return await me(user)
