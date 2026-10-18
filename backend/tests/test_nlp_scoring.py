import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.services.nlp.embeddings import HashingEmbedder, novelty
from app.services.nlp.scoring import (
    DecayItem,
    decayed_sentiment,
    impact,
    sentiment_features,
)
from app.services.nlp.sentiment import LexiconSentiment, article_sentiment


def test_lexicon_sentiment_signs_and_negation():
    m = LexiconSentiment()
    pos, neg, negated, neutral = m.score(
        [
            "Company beats estimates and raises guidance on strong growth",
            "Shares plunge after weak results and a downgrade",
            "Revenue did not grow and margins failed to improve",
            "The company will hold its annual meeting on Tuesday",
        ]
    )
    assert pos > 0.3
    assert neg < -0.3
    assert negated < 0
    assert neutral == 0.0
    assert all(-1 <= s <= 1 for s in (pos, neg, negated, neutral))


def test_article_sentiment_weights_headline_and_body():
    class Fixed:
        def score(self, texts):
            return [1.0] + [-1.0] * (len(texts) - 1)

    assert article_sentiment(Fixed(), "h", "body text") == pytest.approx(0.6 - 0.4)
    assert article_sentiment(Fixed(), "h", "body", social=True) == 1.0
    assert article_sentiment(Fixed(), "h", "") == 1.0


def test_hashing_embedder_novelty():
    e = HashingEmbedder()
    a, b, c = e.embed(
        [
            "Nvidia unveils Blackwell AI chip",
            "Nvidia unveils Blackwell AI chip at keynote",
            "Ford recalls trucks over brake issue",
        ]
    )
    assert np.linalg.norm(a) == pytest.approx(1.0)
    assert novelty(b, [a]) < novelty(c, [a])
    assert novelty(a, []) == 1.0
    assert novelty(a, [a]) == pytest.approx(0.1)  # floored


def test_impact_formula():
    i = impact(sentiment=0.5, source_weight=0.8, novelty=0.5, relevance=1.0, event_mult=1.5,
               link_confidence=0.9)
    assert i == pytest.approx(0.5 * 0.8 * 0.5 * 1.0 * 1.5 * 0.9)


def test_decayed_sentiment_half_life():
    now = datetime(2026, 3, 1, 15, tzinfo=UTC)
    items = [DecayItem(1.0, now), DecayItem(1.0, now - timedelta(hours=6))]
    assert decayed_sentiment(items, now, half_life_h=6) == pytest.approx(1.5)
    future = [DecayItem(1.0, now + timedelta(minutes=1))]
    assert decayed_sentiment(future, now) == 0.0  # not yet available


def test_sentiment_features_clip_and_attention():
    hist = np.array([0.0, 1.0, -1.0, 0.5, -0.5] * 12)
    f = sentiment_features(100.0, hist, count_24h=30, daily_counts=np.array([5.0, 6, 4, 5] * 15))
    assert f.sentiment == 1.0
    assert f.attention_z > 3
    assert math.isfinite(f.sentiment_z)
