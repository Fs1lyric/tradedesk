"""URL canonicalization and SimHash near-duplicate clustering."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "cmpid", "guccounter"}
WORD = re.compile(r"[a-z0-9]+")
MASK64 = (1 << 64) - 1


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    if path == "/" and not query:
        path = ""
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def normalize(text: str) -> list[str]:
    return WORD.findall(text.lower())


def _h64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


def simhash64(text: str) -> int:
    words = normalize(text)
    features = [" ".join(words[i : i + 2]) for i in range(max(1, len(words) - 1))] + words
    weights = [0] * 64
    for f in features:
        h = _h64(f)
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit, w in enumerate(weights):
        if w > 0:
            out |= 1 << bit
    return out


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & MASK64).bit_count()


def to_signed64(h: int) -> int:
    return h - (1 << 64) if h >= (1 << 63) else h


def to_unsigned64(s: int) -> int:
    return s & MASK64


@dataclass(frozen=True)
class ClusterCandidate:
    item_id: int
    cluster_id: int
    simhash: int
    available_at: datetime


def find_cluster(
    simhash: int,
    candidates: list[ClusterCandidate],
    now: datetime,
    max_distance: int = 3,
    window: timedelta = timedelta(hours=48),
) -> int | None:
    """Cluster id of the closest candidate within the window and distance, else None."""
    best: tuple[int, int] | None = None
    for c in candidates:
        if now - c.available_at > window:
            continue
        d = hamming(simhash, to_unsigned64(c.simhash))
        if d <= max_distance and (best is None or d < best[0]):
            best = (d, c.cluster_id)
    return None if best is None else best[1]
