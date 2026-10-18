"""Rule-based news event classifier. Each event type carries an impact multiplier."""

from __future__ import annotations

import re

EVENT_MULTIPLIERS: dict[str, float] = {
    "earnings": 1.5,
    "guidance": 1.5,
    "mna": 1.5,
    "regulatory": 1.4,
    "legal": 1.2,
    "analyst": 0.8,
    "product": 0.6,
    "macro": 0.5,
    "other": 0.3,
}

# Checked in order; the first match wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("mna", re.compile(r"\b(acquir\w*|merger|merge|buyout|takeover|tender offer)\b", re.I)),
    ("regulatory", re.compile(r"\b(FDA|SEC approv\w*|FTC|antitrust|EMA|clearance)\b")),
    (
        "legal",
        re.compile(r"\b(lawsuit|sued|sues|probe|investigation|indict\w*|settle\w*|fine[sd]?)\b", re.I),
    ),
    ("guidance", re.compile(r"\b(guidance|outlook|forecast)\b", re.I)),
    (
        "earnings",
        re.compile(r"\b(earnings|EPS|quarterly results|revenue|Q[1-4] results|beats|misses)\b"),
    ),
    (
        "analyst",
        re.compile(r"\b(upgrad\w*|downgrad\w*|price target|initiat\w* coverage|reiterat\w*)\b", re.I),
    ),
    ("product", re.compile(r"\b(launch\w*|unveil\w*|releases?|recall\w*)\b", re.I)),
    (
        "macro",
        re.compile(r"\b(Federal Reserve|Fed|CPI|inflation|jobs report|payrolls|GDP|rates?)\b"),
    ),
]


def classify_event(title: str, body: str) -> tuple[str, float]:
    """Classify on the headline first, then the lead of the body."""
    lead = (body or "")[:500]
    for text in (title, lead):
        for label, pattern in _RULES:
            if pattern.search(text):
                return label, EVENT_MULTIPLIERS[label]
    return "other", EVENT_MULTIPLIERS["other"]
