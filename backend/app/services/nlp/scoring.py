"""Item impact and per-ticker decayed sentiment features."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.services.signals.indicators import zscore


def impact(
    sentiment: float,
    source_weight: float,
    novelty: float,
    relevance: float,
    event_mult: float,
    link_confidence: float,
) -> float:
    return sentiment * source_weight * novelty * relevance * event_mult * link_confidence


@dataclass(frozen=True)
class DecayItem:
    impact: float
    available_at: datetime


def decayed_sentiment(items: Iterable[DecayItem], now: datetime, half_life_h: float = 6.0) -> float:
    """Sum of impacts with exponential decay. Items not yet available at `now` are ignored."""
    lam = math.log(2) / half_life_h
    total = 0.0
    for item in items:
        age_h = (now - item.available_at).total_seconds() / 3600
        if age_h < 0:
            continue
        total += item.impact * math.exp(-lam * age_h)
    return total


@dataclass(frozen=True)
class SentimentFeatures:
    sentiment: float  # clip(z, -3, 3) / 3, in [-1, 1]
    sentiment_z: float
    attention_z: float


def sentiment_features(
    s_raw: float, s_raw_history: np.ndarray, count_24h: int, daily_counts: np.ndarray
) -> SentimentFeatures:
    z = zscore(s_raw, s_raw_history)
    attn = zscore(float(count_24h), daily_counts)
    return SentimentFeatures(float(np.clip(z, -3, 3) / 3), z, attn)
