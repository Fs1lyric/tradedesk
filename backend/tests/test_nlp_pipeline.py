from datetime import UTC, datetime

import pytest

from app.services.nlp.dedup import ClusterCandidate, simhash64
from app.services.nlp.embeddings import HashingEmbedder
from app.services.nlp.linking import AliasIndex
from app.services.nlp.pipeline import NewsContext, RawNewsItem, process_item
from app.services.nlp.sentiment import LexiconSentiment

NOW = datetime(2026, 5, 1, 14, tzinfo=UTC)


def ctx(candidates=(), prior=None):
    return NewsContext(
        symbols={"AAPL", "MSFT"},
        aliases=AliasIndex({"Apple": "AAPL"}),
        sentiment=LexiconSentiment(),
        embedder=HashingEmbedder(),
        cluster_candidates=lambda: list(candidates),
        prior_embeddings=lambda symbol: (prior or {}).get(symbol, []),
    )


def item(title, body="", **kw):
    return RawNewsItem(
        url="https://www.example.com/a?utm_source=x",
        title=title,
        body=body,
        source_credibility=1.0,
        social=False,
        available_at=NOW,
        **kw,
    )


def test_process_item_scores_linked_tickers():
    out = process_item(item("Apple beats earnings estimates on strong iPhone growth"), ctx())
    assert out.url_canonical == "https://example.com/a"
    assert out.event_type == "earnings"
    assert out.sentiment > 0
    assert out.cluster_id is None
    (t,) = out.tickers
    assert t.symbol == "AAPL"
    assert t.impact > 0
    assert t.impact == pytest.approx(out.sentiment * t.novelty * 1.5 * 0.85)


def test_duplicate_joins_existing_cluster():
    title = "Apple beats earnings estimates on strong iPhone growth"
    cand = ClusterCandidate(item_id=7, cluster_id=7, simhash=simhash64(title), available_at=NOW)
    out = process_item(item(title), ctx([cand]))
    assert out.cluster_id == 7
    assert out.is_duplicate


def test_no_tickers_means_no_impact_rows():
    out = process_item(item("Markets mixed in quiet trade"), ctx())
    assert out.tickers == []
