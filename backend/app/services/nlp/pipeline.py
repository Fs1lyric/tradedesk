"""One news item through dedup, linking, classification, sentiment and impact."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app.services.nlp.dedup import ClusterCandidate, canonical_url, find_cluster, simhash64
from app.services.nlp.embeddings import Embedder, novelty
from app.services.nlp.events import classify_event
from app.services.nlp.linking import AliasIndex, link_tickers
from app.services.nlp.scoring import impact
from app.services.nlp.sentiment import SentimentModel, article_sentiment

SIMHASH_LEAD_CHARS = 300


@dataclass(frozen=True)
class RawNewsItem:
    url: str
    title: str
    body: str
    source_credibility: float
    social: bool
    available_at: datetime
    published_at: datetime | None = None


@dataclass
class NewsContext:
    symbols: set[str]
    aliases: AliasIndex
    sentiment: SentimentModel
    embedder: Embedder
    cluster_candidates: Callable[[], list[ClusterCandidate]]
    prior_embeddings: Callable[[str], list[np.ndarray]]


@dataclass(frozen=True)
class ScoredTicker:
    symbol: str
    confidence: float
    relevance: float
    novelty: float
    impact: float


@dataclass
class ScoredNews:
    url_canonical: str
    simhash: int
    cluster_id: int | None
    event_type: str
    event_mult: float
    sentiment: float
    novelty: float
    embedding: np.ndarray
    tickers: list[ScoredTicker] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.cluster_id is not None


def process_item(raw: RawNewsItem, ctx: NewsContext) -> ScoredNews:
    h = simhash64(f"{raw.title} {(raw.body or '')[:SIMHASH_LEAD_CHARS]}")
    cluster = find_cluster(h, ctx.cluster_candidates(), raw.available_at)
    event_type, mult = classify_event(raw.title, raw.body)
    sent = article_sentiment(ctx.sentiment, raw.title, raw.body, social=raw.social)
    (vec,) = ctx.embedder.embed([f"{raw.title}. {(raw.body or '')[:1000]}"])

    tickers = []
    for link in link_tickers(raw.title, raw.body, ctx.symbols, ctx.aliases):
        nov = novelty(vec, ctx.prior_embeddings(link.symbol))
        tickers.append(
            ScoredTicker(
                link.symbol,
                link.confidence,
                link.relevance,
                nov,
                impact(sent, raw.source_credibility, nov, link.relevance, mult, link.confidence),
            )
        )
    return ScoredNews(
        url_canonical=canonical_url(raw.url),
        simhash=h,
        cluster_id=cluster,
        event_type=event_type,
        event_mult=mult,
        sentiment=sent,
        novelty=min((t.novelty for t in tickers), default=1.0),
        embedding=vec,
        tickers=tickers,
    )
