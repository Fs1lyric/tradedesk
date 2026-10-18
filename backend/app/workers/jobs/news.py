"""News ingestion: fetch (RSS, Alpaca News), score through the NLP pipeline, store."""

from __future__ import annotations

import calendar
import html
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import NewsItem, NewsSource, NewsTicker, Symbol
from app.services.brokers.base import NewsArticle
from app.services.nlp.dedup import ClusterCandidate, canonical_url, to_signed64
from app.services.nlp.embeddings import Embedder, HashingEmbedder
from app.services.nlp.linking import AliasIndex
from app.services.nlp.pipeline import NewsContext, RawNewsItem, process_item
from app.services.nlp.sentiment import LexiconSentiment, SentimentModel

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
CLUSTER_LOOKBACK = timedelta(hours=72)
NOVELTY_LOOKBACK = timedelta(days=30)
NOVELTY_PRIOR = 50


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def parse_rss(text: str, credibility: float, social: bool, now: datetime) -> list[RawNewsItem]:
    out = []
    for e in feedparser.parse(text).entries:
        link, title = e.get("link"), _text(e.get("title", ""))
        if not link or not title:
            continue
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(parsed), UTC) if parsed else None
        out.append(RawNewsItem(
            url=link, title=title, body=_text(e.get("summary", "")),
            source_credibility=credibility, social=social, available_at=now,
            published_at=published,
        ))
    return out


def from_alpaca(articles: Sequence[NewsArticle], credibility: float, now: datetime | None
                ) -> list[RawNewsItem]:
    """With now=None (a backfill) each item is available from its own timestamp."""
    return [RawNewsItem(
        url=a.url or f"alpaca://news/{a.id}", title=a.headline,
        body="\n\n".join(p for p in (a.summary, a.body) if p), source_credibility=credibility,
        social=False, available_at=now or a.created_at, published_at=a.created_at,
    ) for a in articles]


class NewsIngestor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sentiment: SentimentModel | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.sessions = sessions
        self.sentiment = sentiment or LexiconSentiment()
        self.embedder = embedder or HashingEmbedder()

    async def _context(self, s: AsyncSession, now: datetime
                       ) -> tuple[NewsContext, list[ClusterCandidate], dict[str, list[np.ndarray]]]:
        symbols = list(await s.scalars(select(Symbol).where(Symbol.tradable)))
        aliases: dict[str, str] = {}
        for row in symbols:
            for name in [row.name, *row.aliases]:
                aliases.setdefault(name, row.symbol)
        rows = await s.execute(
            select(NewsItem.id, NewsItem.cluster_id, NewsItem.simhash, NewsItem.available_at)
            .where(NewsItem.available_at >= now - CLUSTER_LOOKBACK)
        )
        candidates = [ClusterCandidate(i, c if c is not None else i, h, at)
                      for i, c, h, at in rows]
        priors: dict[str, list[np.ndarray]] = {}

        def prior_embeddings(symbol: str) -> list[np.ndarray]:
            return priors.get(symbol, [])

        ctx = NewsContext(
            symbols={row.symbol for row in symbols}, aliases=AliasIndex(aliases),
            sentiment=self.sentiment, embedder=self.embedder,
            cluster_candidates=lambda: candidates, prior_embeddings=prior_embeddings,
        )
        # Prior embeddings per symbol, newest first, loaded up front for the batch.
        emb = await s.execute(
            select(NewsTicker.symbol, NewsItem.embedding)
            .join(NewsItem, NewsItem.id == NewsTicker.news_id)
            .where(NewsItem.available_at >= now - NOVELTY_LOOKBACK,
                   NewsItem.embedding.is_not(None))
            .order_by(NewsItem.available_at.desc())
        )
        for ticker, vec in emb:
            lst = priors.setdefault(ticker, [])
            if len(lst) < NOVELTY_PRIOR:
                lst.append(np.asarray(vec, dtype=float))
        return ctx, candidates, priors

    async def ingest(self, raws: Sequence[RawNewsItem], source_id: int | None) -> list[int]:
        """Store what's new. Returns ids of new items that aren't copies of a recent story,
        which are the ones worth acting on."""
        if not raws:
            return []
        now = max(r.available_at for r in raws)
        fresh: list[int] = []
        async with self.sessions.begin() as s:
            ctx, candidates, priors = await self._context(s, now)
            urls = {canonical_url(r.url) for r in raws}
            seen = set(await s.scalars(
                select(NewsItem.url_canonical).where(NewsItem.url_canonical.in_(urls))
            ))
            for raw in sorted(raws, key=lambda r: r.available_at):
                url = canonical_url(raw.url)
                if url in seen:
                    continue
                seen.add(url)
                scored = process_item(raw, ctx)
                item = NewsItem(
                    source_id=source_id, url=raw.url, url_canonical=url, title=raw.title,
                    body=raw.body, published_at=raw.published_at,
                    available_at=raw.available_at, simhash=to_signed64(scored.simhash),
                    cluster_id=scored.cluster_id, event_type=scored.event_type,
                    event_mult=scored.event_mult, sentiment=scored.sentiment,
                    novelty=scored.novelty, embedding=[float(x) for x in scored.embedding],
                )
                s.add(item)
                await s.flush()
                if item.cluster_id is None:  # a new story starts its own cluster
                    item.cluster_id = item.id
                    fresh.append(item.id)
                candidates.append(ClusterCandidate(item.id, item.cluster_id, item.simhash,
                                                   item.available_at))
                for t in scored.tickers:
                    s.add(NewsTicker(news_id=item.id, symbol=t.symbol, confidence=t.confidence,
                                     relevance=t.relevance, novelty=t.novelty, impact=t.impact))
                    priors.setdefault(t.symbol, []).insert(0, scored.embedding)
        return fresh


async def fetch_rss_sources(
    sessions: async_sessionmaker[AsyncSession], client: httpx.AsyncClient,
    ingestor: NewsIngestor, now: datetime,
) -> int:
    """Poll every enabled RSS source; returns how many new stories were stored."""
    async with sessions() as s:
        sources = list(await s.scalars(
            select(NewsSource).where(NewsSource.kind == "rss", NewsSource.enabled,
                                     NewsSource.url.is_not(None))
        ))
    n = 0
    for src in sources:
        assert src.url is not None
        try:
            r = await client.get(src.url, timeout=20, follow_redirects=True)
            r.raise_for_status()
            raws = parse_rss(r.text, src.credibility, src.social, now)
            n += len(await ingestor.ingest(raws, src.id))
        except Exception:
            log.exception("rss source %s failed", src.name)
    return n


async def source_id(sessions: async_sessionmaker[AsyncSession], name: str, kind: str,
                    credibility: float = 0.5) -> int:
    """Get or create a news source by name."""
    async with sessions.begin() as s:
        src = await s.scalar(select(NewsSource).where(NewsSource.name == name))
        if src is None:
            src = NewsSource(name=name, kind=kind, credibility=credibility)
            s.add(src)
            await s.flush()
        return src.id
