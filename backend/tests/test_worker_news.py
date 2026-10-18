from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models import NewsItem, NewsSource, NewsTicker, Symbol
from app.services.nlp.pipeline import RawNewsItem
from app.workers.jobs.news import NewsIngestor, fetch_rss_sources, parse_rss
from tests.conftest import requires_db

NOW = datetime(2026, 3, 2, 15, tzinfo=UTC)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Wire</title>
<item><title>Apple (AAPL) beats estimates</title><link>https://wire.example/a?utm_source=x</link>
<description>&lt;p&gt;Record iPhone sales.&lt;/p&gt;</description>
<pubDate>Mon, 02 Mar 2026 14:30:00 GMT</pubDate></item>
<item><title>Microsoft cuts guidance</title><link>https://wire.example/b</link>
<description>Cloud growth slowed.</description></item>
</channel></rss>"""


def test_parse_rss():
    items = parse_rss(RSS, credibility=0.7, social=False, now=NOW)
    assert [i.title for i in items] == ["Apple (AAPL) beats estimates", "Microsoft cuts guidance"]
    assert items[0].body == "Record iPhone sales."
    assert items[0].published_at == datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    assert items[1].published_at is None
    assert all(i.available_at == NOW and i.source_credibility == 0.7 for i in items)


def raw(url: str, title: str, body: str = "", minutes: int = 0) -> RawNewsItem:
    return RawNewsItem(url=url, title=title, body=body, source_credibility=0.8, social=False,
                       available_at=NOW + timedelta(minutes=minutes))


@pytest.fixture
async def sessions(api):
    sessions = api.app.state.sessions
    async with sessions.begin() as s:
        s.add_all([
            Symbol(symbol="AAPL", name="Apple Inc.", aliases=["Apple"]),
            Symbol(symbol="MSFT", name="Microsoft Corporation", aliases=["Microsoft"]),
        ])
        s.add(NewsSource(name="wire", kind="rss", url="https://wire.example/rss",
                         credibility=0.7))
    return sessions


@requires_db
async def test_ingest_links_dedups_and_clusters(sessions):
    ing = NewsIngestor(sessions)
    title = "Apple beats earnings estimates on record iPhone sales in the holiday quarter"
    new = await ing.ingest([
        raw("https://a.example/1", title, "Apple reported strong results."),
        raw("https://a.example/1?utm_campaign=z", title, minutes=1),  # same canonical url
        raw("https://b.example/2", title, "Apple reported strong results.", minutes=2),
        raw("https://c.example/3", "Microsoft cuts cloud guidance", minutes=3),
    ], source_id=None)
    assert len(new) == 2  # the copy on another site is stored but clustered, not new

    async with sessions() as s:
        items = list(await s.scalars(select(NewsItem).order_by(NewsItem.id)))
        tickers = list(await s.execute(select(NewsTicker.news_id, NewsTicker.symbol)))
    assert len(items) == 3
    assert items[0].cluster_id == items[0].id
    assert items[1].cluster_id == items[0].id
    assert items[2].cluster_id == items[2].id
    assert {(i, sym) for i, sym in tickers} == {
        (items[0].id, "AAPL"), (items[1].id, "AAPL"), (items[2].id, "MSFT"),
    }
    assert items[0].sentiment > 0
    assert items[0].embedding is not None

    again = await NewsIngestor(sessions).ingest([raw("https://a.example/1", title)], None)
    assert again == []


@requires_db
async def test_fetch_rss_sources(sessions):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://wire.example/rss"
        return httpx.Response(200, text=RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        n = await fetch_rss_sources(sessions, client, NewsIngestor(sessions), NOW)
    assert n == 2
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(NewsItem)) == 2
        src = await s.scalar(select(NewsItem.source_id).limit(1))
        assert src is not None
