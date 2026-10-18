from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import NewsItem, NewsSource, NewsTicker, Order, Recommendation
from app.services.brokers.fake import FakeBroker
from tests.conftest import requires_db, signup

pytestmark = requires_db

ACCOUNT = {"broker": "alpaca", "label": "paper", "api_key": "PK123456", "api_secret": "s"}
RULE = {
    "name": "gap up on news",
    "universe": {"symbols": ["AAPL"]},
    "when": {"field": "news.impact", "op": ">", "value": 0.5},
    "action": {"side": "buy"},
}


async def _account(api, headers) -> str:
    r = await api.post("/accounts", headers=headers, json=ACCOUNT)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_rule_crud(api):
    headers = await signup(api)
    acct = await _account(api, headers)
    base = f"/accounts/{acct}/rules"
    r = await api.post(base, headers=headers, json=RULE)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["mode"] == "approve" and rule["enabled"] is True
    assert rule["definition"]["action"]["sizing"] == "atr_risk"

    r = await api.patch(f"/rules/{rule['id']}", headers=headers,
                        json={"enabled": False, "definition": {**RULE, "mode": "auto"}})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["mode"] == "auto"
    assert [x["id"] for x in (await api.get(base, headers=headers)).json()] == [rule["id"]]

    bob = await signup(api, email="b@example.com")
    assert (await api.patch(f"/rules/{rule['id']}", headers=bob, json={})).status_code == 404
    assert (await api.get(base, headers=bob)).status_code == 404
    assert (await api.delete(f"/rules/{rule['id']}", headers=headers)).status_code == 204
    assert (await api.get(base, headers=headers)).json() == []


async def test_rule_validation(api):
    headers = await signup(api)
    acct = await _account(api, headers)
    base = f"/accounts/{acct}/rules"
    bad_op = {**RULE, "when": {"field": "x", "op": "~=", "value": 1}}
    assert (await api.post(base, headers=headers, json=bad_op)).status_code == 422
    bob = await signup(api, email="b@example.com")
    bobs = (await api.post("/watchlists", headers=bob, json={"name": "b"})).json()["id"]
    foreign = {**RULE, "universe": {"watchlist_id": bobs}}
    assert (await api.post(base, headers=headers, json=foreign)).status_code == 422


async def _recommendation(api, acct: str, **kw) -> str:
    row = Recommendation(
        account_id=acct, symbol="AAPL", side="buy", qty=10, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, expected_return=0.02, score=0.7, confidence=0.6,
        rationale={"news": ["beat"]}, **kw,
    )
    async with api.app.state.sessions.begin() as s:
        s.add(row)
    return row.id


def _fake_broker(api) -> FakeBroker:
    broker = FakeBroker(cash=100_000)
    broker.set_quote("AAPL", 99.95, 100.05)
    api.app.state.broker_factory = lambda account, settings: broker
    return broker


async def test_approve_recommendation_places_order(api):
    headers = await signup(api)
    acct = await _account(api, headers)
    broker = _fake_broker(api)
    rec = await _recommendation(api, acct)

    pending = (await api.get(f"/accounts/{acct}/recommendations", headers=headers)).json()
    assert [p["id"] for p in pending] == [rec]

    r = await api.post(f"/recommendations/{rec}/approve", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recommendation"]["status"] == "executed"
    assert body["risk"]["passed"] is True
    assert body["order"]["order_class"] == "bracket"
    assert len(broker.orders) == 1

    async with api.app.state.sessions() as s:
        order = await s.scalar(select(Order))
    assert order.account_id == acct and order.symbol == "AAPL"

    # Only pending recommendations can be acted on.
    assert (await api.post(f"/recommendations/{rec}/approve", headers=headers)).status_code == 409
    assert (await api.get(f"/accounts/{acct}/recommendations", headers=headers)).json() == []


async def test_approve_blocked_by_kill_switch(api):
    headers = await signup(api)
    acct = await _account(api, headers)
    broker = _fake_broker(api)
    await api.patch(f"/accounts/{acct}", headers=headers, json={"kill_switch": True})
    rec = await _recommendation(api, acct)
    r = await api.post(f"/recommendations/{rec}/approve", headers=headers)
    assert r.status_code == 200
    assert r.json()["recommendation"]["status"] == "rejected"
    assert r.json()["order"] is None
    assert "kill_switch" in [c["name"] for c in r.json()["risk"]["checks"] if not c["passed"]]
    assert broker.orders == {}


async def test_expired_and_dismissed_recommendations(api):
    headers = await signup(api)
    acct = await _account(api, headers)
    _fake_broker(api)
    old = await _recommendation(api, acct, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    r = await api.post(f"/recommendations/{old}/approve", headers=headers)
    assert r.status_code == 409
    rec = await _recommendation(api, acct)
    r = await api.post(f"/recommendations/{rec}/dismiss", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    hist = (await api.get(f"/accounts/{acct}/recommendations?status=all",
                          headers=headers)).json()
    assert sorted(h["status"] for h in hist) == ["dismissed", "expired"]

    bob = await signup(api, email="b@example.com")
    assert (await api.post(f"/recommendations/{rec}/dismiss", headers=bob)).status_code == 404


async def test_news_feed(api):
    headers = await signup(api)
    t0 = datetime(2026, 3, 2, 14, tzinfo=UTC)
    async with api.app.state.sessions.begin() as s:
        src = NewsSource(name="wire", kind="rss", credibility=0.8)
        s.add(src)
        await s.flush()
        for i, sym in enumerate(["AAPL", "MSFT", "AAPL"]):
            item = NewsItem(
                source_id=src.id, url=f"https://x/{i}", url_canonical=f"https://x/{i}",
                title=f"story {i}", available_at=t0 + timedelta(minutes=i), simhash=i,
                event_type="earnings", event_mult=1.0, sentiment=0.3, novelty=1.0,
            )
            s.add(item)
            await s.flush()
            s.add(NewsTicker(news_id=item.id, symbol=sym, confidence=0.9, relevance=0.8,
                             novelty=1.0, impact=0.4))

    assert (await api.get("/news")).status_code == 401
    feed = (await api.get("/news", headers=headers)).json()
    assert [n["title"] for n in feed] == ["story 2", "story 1", "story 0"]
    assert feed[0]["source"] == "wire" and feed[0]["tickers"][0]["symbol"] == "AAPL"

    aapl = (await api.get("/news?symbol=aapl", headers=headers)).json()
    assert [n["title"] for n in aapl] == ["story 2", "story 0"]
    page = (await api.get("/news?limit=1", headers=headers)).json()
    assert [n["title"] for n in page] == ["story 2"]
    before = (await api.get(f"/news?before={page[0]['id']}", headers=headers)).json()
    assert [n["title"] for n in before] == ["story 1", "story 0"]

    one = await api.get(f"/news/{feed[1]['id']}", headers=headers)
    assert one.json()["title"] == "story 1"
    assert (await api.get("/news/999999", headers=headers)).status_code == 404
