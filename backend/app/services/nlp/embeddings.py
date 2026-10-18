"""Text embeddings for novelty scoring."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

WORD = re.compile(r"[a-z0-9]+")
DIM = 384
NOVELTY_FLOOR = 0.1


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[np.ndarray]: ...


class HashingEmbedder:
    """Signed feature hashing over words and bigrams, L2-normalized. No model download."""

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> np.ndarray:
        words = WORD.findall(text.lower())
        feats = words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
        v = np.zeros(self.dim)
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v


class SentenceTransformerEmbedder:
    """all-MiniLM-L6-v2 (384 dims). Needs the `nlp` extra."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model: Any = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        return list(self._model.encode(texts, normalize_embeddings=True))


def novelty(vec: np.ndarray, prior: Sequence[np.ndarray]) -> float:
    """1 - max cosine similarity to prior same-ticker items, floored at 0.1."""
    if len(prior) == 0:
        return 1.0
    sims = [float(np.dot(vec, p)) for p in prior]
    return max(NOVELTY_FLOOR, 1.0 - max(sims))
