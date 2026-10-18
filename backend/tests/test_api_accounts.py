from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models import BrokerAccount
from tests.conftest import requires_db, signup

pytestmark = requires_db

NEW = {"broker": "alpaca", "label": "paper 1", "api_key": "PKTEST1234", "api_secret": "s3cret"}


async def test_create_account_encrypts_keys_and_hides_them(api):
    headers = await signup(api)
    r = await api.post("/accounts", headers=headers, json=NEW)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "paper"
    assert body["api_key_hint"] == "…1234"
    assert "api_secret" not in body and "api_key" not in body
    assert body["risk_limits"]["max_daily_loss_pct"] == 0.02

    async with api.app.state.sessions() as s:
        row = await s.scalar(select(BrokerAccount))
    key = api.app.state.settings.master_key_bytes
    assert decrypt_secret(row.api_key_enc, key) == "PKTEST1234"
    assert decrypt_secret(row.api_secret_enc, key) == "s3cret"


async def test_live_account_needs_live_trading_enabled(api):
    headers = await signup(api)
    r = await api.post("/accounts", headers=headers, json={**NEW, "mode": "live"})
    assert r.status_code == 403


async def test_unknown_broker_rejected(api):
    headers = await signup(api)
    r = await api.post("/accounts", headers=headers, json={**NEW, "broker": "robinhood"})
    assert r.status_code == 422


async def test_patch_limits_and_kill_switch(api):
    headers = await signup(api)
    acct = (await api.post("/accounts", headers=headers, json=NEW)).json()
    url = f"/accounts/{acct['id']}"
    r = await api.patch(url, headers=headers,
                        json={"kill_switch": True, "risk_limits": {"max_position_pct": 0.05}})
    assert r.status_code == 200, r.text
    assert r.json()["kill_switch"] is True
    assert r.json()["risk_limits"]["max_position_pct"] == 0.05
    assert r.json()["risk_limits"]["max_daily_loss_pct"] == 0.02

    r = await api.patch(url, headers=headers, json={"risk_limits": {"bogus": 1}})
    assert r.status_code == 422
    r = await api.patch(url, headers=headers, json={"risk_limits": {"max_position_pct": -1}})
    assert r.status_code == 422


async def test_accounts_are_private(api):
    alice = await signup(api)
    bob = await signup(api, email="b@example.com")
    acct = (await api.post("/accounts", headers=alice, json=NEW)).json()
    assert (await api.get("/accounts", headers=bob)).json() == []
    assert (await api.get(f"/accounts/{acct['id']}", headers=bob)).status_code == 404
    assert (await api.delete(f"/accounts/{acct['id']}", headers=bob)).status_code == 404
    assert (await api.get("/accounts/not-a-uuid", headers=alice)).status_code == 404
    assert (await api.delete(f"/accounts/{acct['id']}", headers=alice)).status_code == 204
    assert (await api.get("/accounts", headers=alice)).json() == []


async def test_watchlists(api):
    headers = await signup(api)
    r = await api.post("/watchlists", headers=headers, json={"name": "tech"})
    assert r.status_code == 201
    wl = r.json()
    assert wl["symbols"] == []
    url = f"/watchlists/{wl['id']}"

    assert (await api.put(f"{url}/symbols/aapl", headers=headers)).status_code == 204
    assert (await api.put(f"{url}/symbols/AAPL", headers=headers)).status_code == 204  # idempotent
    assert (await api.put(f"{url}/symbols/BRK.B", headers=headers)).status_code == 204
    assert (await api.put(f"{url}/symbols/no%20way", headers=headers)).status_code == 422

    lists = (await api.get("/watchlists", headers=headers)).json()
    assert [w["symbols"] for w in lists] == [["AAPL", "BRK.B"]]

    assert (await api.delete(f"{url}/symbols/AAPL", headers=headers)).status_code == 204
    assert (await api.get("/watchlists", headers=headers)).json()[0]["symbols"] == ["BRK.B"]

    bob = await signup(api, email="b@example.com")
    assert (await api.put(f"{url}/symbols/MSFT", headers=bob)).status_code == 404
    assert (await api.delete(url, headers=bob)).status_code == 404
    assert (await api.delete(url, headers=headers)).status_code == 204
    assert (await api.get("/watchlists", headers=headers)).json() == []
