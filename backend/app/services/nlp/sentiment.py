"""Sentiment models. Scores are in [-1, 1] (p_pos - p_neg for FinBERT)."""

from __future__ import annotations

import re
from typing import Any, Protocol

WORD = re.compile(r"[a-z']+")

POSITIVE = {
    "beat", "beats", "surge", "soar", "jump", "rally", "gain", "rise", "raise", "record",
    "strong", "growth", "grow", "improve", "upgrade", "outperform", "profit", "profitable",
    "exceed", "boost", "expand", "accelerate", "approve", "approval", "win", "bullish",
    "rebound", "robust", "upbeat", "optimistic", "momentum", "breakthrough", "top",
}
NEGATIVE = {
    "miss", "misses", "plunge", "tumble", "slump", "drop", "fall", "decline", "cut", "weak",
    "loss", "losses", "downgrade", "underperform", "lawsuit", "probe", "recall", "warn",
    "warning", "bankruptcy", "default", "fraud", "layoff", "layoffs", "slow", "slowdown",
    "bearish", "concern", "risk", "delay", "halt", "investigation", "sink", "crash", "lower",
}
NEGATORS = {"not", "no", "never", "without", "fail", "failed", "fails", "didn't", "isn't"}
NEGATION_WINDOW = 3
BODY_CHUNK_WORDS = 350


class SentimentModel(Protocol):
    def score(self, texts: list[str]) -> list[float]: ...


def _stems(word: str) -> tuple[str, ...]:
    out = [word]
    for suffix in ("ing", "ed", "es", "s", "d"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            out.append(word[: -len(suffix)])
    return tuple(out)


class LexiconSentiment:
    """Finance word-list model with simple negation; the dependency-free default."""

    def score(self, texts: list[str]) -> list[float]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> float:
        pos = neg = 0
        negate_left = 0
        for word in WORD.findall(text.lower()):
            if word in NEGATORS:
                negate_left = NEGATION_WINDOW
                continue
            stems = _stems(word)
            polarity = 0
            if any(s in POSITIVE for s in stems):
                polarity = 1
            elif any(s in NEGATIVE for s in stems):
                polarity = -1
            if negate_left:
                polarity = -polarity
                negate_left -= 1
            if polarity > 0:
                pos += 1
            elif polarity < 0:
                neg += 1
        return (pos - neg) / (pos + neg + 1)


class FinBertSentiment:
    """ProsusAI/finbert via transformers. Needs the `nlp` extra."""

    def __init__(self, model_name: str = "ProsusAI/finbert", device: int = -1):
        from transformers import pipeline

        self._pipe: Any = pipeline(
            "text-classification", model=model_name, top_k=None, device=device, truncation=True
        )

    def score(self, texts: list[str]) -> list[float]:
        out = []
        for result in self._pipe(texts):
            probs = {r["label"].lower(): r["score"] for r in result}
            out.append(probs.get("positive", 0.0) - probs.get("negative", 0.0))
        return out


def chunk_words(text: str, size: int = BODY_CHUNK_WORDS) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def article_sentiment(
    model: SentimentModel, title: str, body: str, social: bool = False
) -> float:
    """0.6 * headline + 0.4 * mean(body chunks); headline only for social posts or empty bodies."""
    chunks = [] if social else chunk_words(body or "")
    scores = model.score([title, *chunks])
    if not chunks:
        return scores[0]
    return 0.6 * scores[0] + 0.4 * sum(scores[1:]) / len(chunks)
