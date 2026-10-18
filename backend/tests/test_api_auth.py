import pyotp
import pytest

from tests.conftest import requires_db, signup

pytestmark = requires_db


async def test_register_login_me(api):
    headers = await signup(api)
    r = await api.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"
    assert r.json()["totp_enabled"] is False


async def test_register_rejects_duplicate_and_short_password(api):
    await signup(api)
    body = {"email": "a@example.com", "password": "another pw 12"}
    r = await api.post("/auth/register", json=body)
    assert r.status_code == 409
    r = await api.post("/auth/register", json={"email": "b@example.com", "password": "short"})
    assert r.status_code == 422


async def test_login_wrong_password(api):
    await signup(api)
    r = await api.post("/auth/login", json={"email": "a@example.com", "password": "nope nope nope"})
    assert r.status_code == 401
    r = await api.post("/auth/login", json={"email": "x@example.com", "password": "nope nope nope"})
    assert r.status_code == 401


@pytest.mark.parametrize("header", [None, "Bearer junk", "Basic abc"])
async def test_me_requires_valid_access_token(api, header):
    headers = {"Authorization": header} if header else {}
    r = await api.get("/auth/me", headers=headers)
    assert r.status_code == 401


async def _login(api, **extra) -> dict:
    body = {"email": "a@example.com", "password": "correct horse 1", **extra}
    r = await api.post("/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_refresh_rotates_and_detects_reuse(api):
    await signup(api)
    first = (await _login(api))["refresh_token"]
    r = await api.post("/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 200
    second = r.json()["refresh_token"]
    assert second != first
    # The old token is spent. Replaying it looks like theft, so every session is revoked.
    r = await api.post("/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 401
    r = await api.post("/auth/refresh", json={"refresh_token": second})
    assert r.status_code == 401


async def test_access_token_is_not_a_refresh_token(api):
    await signup(api)
    access = (await _login(api))["access_token"]
    r = await api.post("/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


async def test_logout_revokes_refresh_token(api):
    await signup(api)
    refresh = (await _login(api))["refresh_token"]
    r = await api.post("/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 204
    r = await api.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_totp_enrol_then_login_requires_code(api):
    headers = await signup(api)
    r = await api.post("/auth/totp/enroll", headers=headers)
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert r.json()["uri"].startswith("otpauth://")

    r = await api.post("/auth/totp/confirm", headers=headers,
                       json={"secret": secret, "code": "000000"})
    assert r.status_code == 400
    code = pyotp.TOTP(secret).now()
    r = await api.post("/auth/totp/confirm", headers=headers, json={"secret": secret, "code": code})
    assert r.status_code == 204
    assert (await api.get("/auth/me", headers=headers)).json()["totp_enabled"] is True

    body = {"email": "a@example.com", "password": "correct horse 1"}
    r = await api.post("/auth/login", json=body)
    assert r.status_code == 401
    assert r.json()["detail"] == "totp required"
    await _login(api, totp_code=pyotp.TOTP(secret).now())
