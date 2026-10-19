from app.models import Symbol
from tests.conftest import requires_db, signup

pytestmark = requires_db

PAGE = {
    "url": "https://news.example.com/apple-beats?utm_source=x",
    "title": "Apple beats estimates and raises guidance",
    "body": "Apple Inc. reported record revenue and raised its full-year guidance, "
            "sending shares higher in after-hours trading.",
    "published_at": "2026-10-19T12:00:00Z",
}


async def test_capture_scores_the_page_and_is_idempotent(api):
    async with api.app.state.sessions.begin() as s:
        s.add(Symbol(symbol="AAPL", name="Apple Inc.", aliases=["Apple"]))
    headers = await signup(api)
    r = await api.post("/news/capture", headers=headers, json=PAGE)
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["stored"] is True and first["item"]["source"] == "extension"
    assert [t["symbol"] for t in first["item"]["tickers"]] == ["AAPL"]
    assert first["item"]["body"].startswith("Apple Inc. reported")

    again = {**PAGE, "url": "https://news.example.com/apple-beats"}  # same canonical url
    r = await api.post("/news/capture", headers=headers, json=again)
    assert r.status_code == 200
    assert r.json()["stored"] is False and r.json()["item"]["id"] == first["item"]["id"]


async def test_capture_validates_input(api):
    headers = await signup(api)
    assert (await api.post("/news/capture", json=PAGE)).status_code == 401
    bad = {**PAGE, "url": "javascript:alert(1)"}
    assert (await api.post("/news/capture", headers=headers, json=bad)).status_code == 422
    empty = {**PAGE, "body": "  ", "title": ""}
    assert (await api.post("/news/capture", headers=headers, json=empty)).status_code == 422
