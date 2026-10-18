import pyotp
from sqlalchemy import select

from app.models import BrokerAccount
from tests.conftest import requires_db, signup

pytestmark = requires_db

ACCOUNT = {"broker": "alpaca", "label": "live", "api_key": "AK123456", "api_secret": "s"}


async def _totp(api, headers) -> str:
    secret = (await api.post("/auth/totp/enroll", headers=headers)).json()["secret"]
    r = await api.post("/auth/totp/confirm", headers=headers,
                       json={"secret": secret, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 204
    return secret


async def test_enable_live_needs_totp_and_a_fresh_code(api):
    headers = await signup(api)
    r = await api.post("/auth/live/enable", headers=headers, json={"code": "123456"})
    assert r.status_code == 403
    secret = await _totp(api, headers)
    r = await api.post("/auth/live/enable", headers=headers, json={"code": "000000"})
    assert r.status_code == 400
    r = await api.post("/auth/live/enable", headers=headers,
                       json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and r.json()["live_trading_enabled"] is True
    r = await api.post("/accounts", headers=headers, json={**ACCOUNT, "mode": "live"})
    assert r.status_code == 201


async def test_disable_live_kills_live_accounts(api):
    headers = await signup(api)
    secret = await _totp(api, headers)
    await api.post("/auth/live/enable", headers=headers, json={"code": pyotp.TOTP(secret).now()})
    live = (await api.post("/accounts", headers=headers, json={**ACCOUNT, "mode": "live"})).json()
    paper = (await api.post("/accounts", headers=headers, json=ACCOUNT)).json()
    r = await api.post("/auth/live/disable", headers=headers)
    assert r.status_code == 200 and r.json()["live_trading_enabled"] is False
    async with api.app.state.sessions() as s:
        kill = dict((await s.execute(select(BrokerAccount.id, BrokerAccount.kill_switch))).all())
    assert kill == {live["id"]: True, paper["id"]: False}


async def test_new_brokers_accepted_and_schwab_secret_checked(api):
    headers = await signup(api)
    ibkr = {"broker": "ibkr", "label": "ib", "api_key": "U1234567",
            "api_secret": "https://localhost:5000"}
    assert (await api.post("/accounts", headers=headers, json=ibkr)).status_code == 201
    schwab = {"broker": "schwab", "label": "sw", "api_key": "HASH1234",
              "api_secret": '{"app_key": "k", "app_secret": "s", "refresh_token": "r"}'}
    assert (await api.post("/accounts", headers=headers, json=schwab)).status_code == 201
    bad = {**schwab, "api_secret": "not json"}
    assert (await api.post("/accounts", headers=headers, json=bad)).status_code == 422
