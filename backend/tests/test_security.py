import base64
import os
from datetime import UTC, datetime, timedelta

import pyotp
import pytest

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
    verify_password,
    verify_totp,
)

KEY = base64.b64encode(os.urandom(32)).decode()
SETTINGS = Settings(jwt_secret="x" * 32, master_key=KEY)
NOW = datetime(2026, 3, 2, 15, tzinfo=UTC)


def test_settings_decode_master_key():
    assert len(SETTINGS.master_key_bytes) == 32


def test_settings_reject_short_master_key():
    with pytest.raises(ValueError):
        Settings(jwt_secret="x" * 32, master_key=base64.b64encode(b"short").decode())


def test_password_hash_round_trip():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password(h, "correct horse")
    assert not verify_password(h, "wrong")


def test_access_token_round_trip():
    tok = create_access_token("user-1", SETTINGS, now=NOW)
    claims = decode_token(tok, SETTINGS, expected_type="access", now=NOW + timedelta(minutes=1))
    assert claims["sub"] == "user-1"


def test_expired_access_token_is_rejected():
    tok = create_access_token("user-1", SETTINGS, now=NOW)
    later = NOW + timedelta(minutes=SETTINGS.access_token_minutes + 1)
    with pytest.raises(TokenError):
        decode_token(tok, SETTINGS, expected_type="access", now=later)


def test_refresh_token_is_not_an_access_token():
    tok = create_refresh_token("user-1", SETTINGS, now=NOW)
    assert decode_token(tok, SETTINGS, expected_type="refresh", now=NOW)["jti"]
    with pytest.raises(TokenError):
        decode_token(tok, SETTINGS, expected_type="access", now=NOW)


def test_token_signed_with_other_secret_is_rejected():
    other = Settings(jwt_secret="y" * 32, master_key=KEY)
    tok = create_access_token("user-1", other, now=NOW)
    with pytest.raises(TokenError):
        decode_token(tok, SETTINGS, expected_type="access", now=NOW)


def test_envelope_encryption_round_trip():
    blob = encrypt_secret("alpaca-secret", SETTINGS.master_key_bytes)
    assert b"alpaca-secret" not in blob
    assert decrypt_secret(blob, SETTINGS.master_key_bytes) == "alpaca-secret"


def test_envelope_encryption_uses_fresh_data_key():
    a = encrypt_secret("same", SETTINGS.master_key_bytes)
    b = encrypt_secret("same", SETTINGS.master_key_bytes)
    assert a != b


def test_tampered_ciphertext_fails():
    blob = bytearray(encrypt_secret("alpaca-secret", SETTINGS.master_key_bytes))
    blob[-1] ^= 1
    with pytest.raises(ValueError):
        decrypt_secret(bytes(blob), SETTINGS.master_key_bytes)


def test_wrong_master_key_fails():
    blob = encrypt_secret("alpaca-secret", SETTINGS.master_key_bytes)
    with pytest.raises(ValueError):
        decrypt_secret(blob, os.urandom(32))


def test_totp_accepts_current_code_only():
    secret = new_totp_secret()
    code = pyotp.TOTP(secret).at(NOW)
    assert verify_totp(secret, code, now=NOW)
    assert not verify_totp(secret, code, now=NOW + timedelta(minutes=5))
    assert not verify_totp(secret, "000000x", now=NOW)
