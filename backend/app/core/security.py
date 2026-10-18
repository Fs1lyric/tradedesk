"""Passwords (Argon2id), JWTs, envelope encryption for broker secrets, and TOTP."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings

_hasher = PasswordHasher()
_ALG = "HS256"
_ENVELOPE_VERSION = 1
_NONCE = 12
_WRAPPED_DEK = 32 + 16  # 32-byte data key plus the GCM tag


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _token(sub: str, typ: str, ttl: timedelta, settings: Settings, now: datetime | None) -> str:
    iat = _now(now)
    claims = {"sub": sub, "type": typ, "iat": iat, "exp": iat + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALG)


def create_access_token(sub: str, settings: Settings, now: datetime | None = None) -> str:
    ttl = timedelta(minutes=settings.access_token_minutes)
    return _token(sub, "access", ttl, settings, now)


def create_refresh_token(sub: str, settings: Settings, now: datetime | None = None) -> str:
    ttl = timedelta(days=settings.refresh_token_days)
    return _token(sub, "refresh", ttl, settings, now)


def decode_token(
    token: str, settings: Settings, expected_type: str, now: datetime | None = None
) -> dict[str, Any]:
    try:
        # Expiry is checked here against `now` so tests can pin the clock.
        claims: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[_ALG],
            options={"verify_exp": False, "require": ["sub", "exp", "type", "jti"]},
        )
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e
    if claims["type"] != expected_type:
        raise TokenError(f"expected {expected_type} token")
    if _now(now).timestamp() >= claims["exp"]:
        raise TokenError("token expired")
    return claims


def encrypt_secret(plaintext: str, master_key: bytes) -> bytes:
    """Encrypt with a fresh data key, then wrap the data key with the master key.

    Layout: version(1) | dek_nonce(12) | wrapped_dek(48) | nonce(12) | ciphertext+tag.
    """
    dek = AESGCM.generate_key(bit_length=256)
    dek_nonce, nonce = os.urandom(_NONCE), os.urandom(_NONCE)
    wrapped = AESGCM(master_key).encrypt(dek_nonce, dek, b"dek")
    ct = AESGCM(dek).encrypt(nonce, plaintext.encode(), None)
    return bytes([_ENVELOPE_VERSION]) + dek_nonce + wrapped + nonce + ct


def decrypt_secret(blob: bytes, master_key: bytes) -> str:
    if not blob or blob[0] != _ENVELOPE_VERSION:
        raise ValueError("unknown envelope version")
    i = 1
    dek_nonce = blob[i:i + _NONCE]
    i += _NONCE
    wrapped = blob[i:i + _WRAPPED_DEK]
    i += _WRAPPED_DEK
    nonce, ct = blob[i:i + _NONCE], blob[i + _NONCE:]
    try:
        dek = AESGCM(master_key).decrypt(dek_nonce, wrapped, b"dek")
        return AESGCM(dek).decrypt(nonce, ct, None).decode()
    except InvalidTag as e:
        raise ValueError("secret failed authentication") from e


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str, settings: Settings) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=settings.totp_issuer)


def verify_totp(secret: str, code: str, now: datetime | None = None) -> bool:
    if not (len(code) == 6 and code.isdigit()):
        return False
    return pyotp.TOTP(secret).verify(code, for_time=_now(now), valid_window=1)
