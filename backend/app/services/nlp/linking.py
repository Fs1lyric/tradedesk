"""Map article text to ticker symbols with a confidence and a relevance weight."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
EXCHANGE_TICKER = re.compile(
    r"\((?:NASDAQ|Nasdaq|NYSE|NYSE American|AMEX|CBOE|OTC|TSX)\s*:\s*([A-Z]{1,5}(?:\.[A-Z])?)\)"
)
PAREN_TICKER = re.compile(r"\(([A-Z]{2,5})\)")

CONF_CASHTAG = 0.95
CONF_EXCHANGE = 0.9
CONF_ALIAS = 0.85
CONF_PAREN = 0.8
MIN_CONFIDENCE = 0.6

REL_HEADLINE = 1.0
REL_LEAD = 0.7
REL_BODY = 0.4


@dataclass(frozen=True)
class TickerLink:
    symbol: str
    confidence: float
    relevance: float


class AliasIndex:
    """Company-name aliases matched case-sensitively on word boundaries, longest first."""

    def __init__(self, aliases: Mapping[str, str]):
        self._aliases = dict(aliases)
        names = sorted(self._aliases, key=len, reverse=True)
        self._pattern = (
            re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b") if names else None
        )

    def find(self, text: str) -> set[str]:
        if self._pattern is None:
            return set()
        return {self._aliases[m.group(1)] for m in self._pattern.finditer(text)}


def _matches(text: str, symbols: set[str], index: AliasIndex) -> dict[str, float]:
    found: dict[str, float] = {}

    def add(sym: str, conf: float) -> None:
        if sym in symbols and conf > found.get(sym, 0.0):
            found[sym] = conf

    for m in CASHTAG.finditer(text):
        add(m.group(1), CONF_CASHTAG)
    for m in EXCHANGE_TICKER.finditer(text):
        add(m.group(1), CONF_EXCHANGE)
    for m in PAREN_TICKER.finditer(text):
        add(m.group(1), CONF_PAREN)
    for sym in index.find(text):
        add(sym, CONF_ALIAS)
    return found


def link_tickers(
    title: str, body: str, symbols: Iterable[str], index: AliasIndex
) -> list[TickerLink]:
    universe = set(symbols)
    paragraphs = [p for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    sections = [(title, REL_HEADLINE)]
    if paragraphs:
        sections.append((paragraphs[0], REL_LEAD))
        sections.append(("\n\n".join(paragraphs[1:]), REL_BODY))

    best: dict[str, TickerLink] = {}
    for text, relevance in sections:
        for sym, conf in _matches(text, universe, index).items():
            prev = best.get(sym)
            best[sym] = TickerLink(
                sym,
                max(conf, prev.confidence if prev else 0.0),
                max(relevance, prev.relevance if prev else 0.0),
            )
    return sorted(
        (link for link in best.values() if link.confidence >= MIN_CONFIDENCE),
        key=lambda link: link.symbol,
    )
