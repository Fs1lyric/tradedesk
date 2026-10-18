from app.services.nlp.events import classify_event
from app.services.nlp.linking import AliasIndex, link_tickers

SYMBOLS = {"AAPL", "MSFT", "NVDA", "F"}
INDEX = AliasIndex({"Apple Inc": "AAPL", "Apple": "AAPL", "Microsoft": "MSFT", "Nvidia": "NVDA"})


def by_symbol(links):
    return {link.symbol: link for link in links}


def test_cashtag_in_headline_is_high_confidence_full_relevance():
    links = by_symbol(link_tickers("$NVDA rips after earnings", "", SYMBOLS, INDEX))
    assert links["NVDA"].confidence == 0.95
    assert links["NVDA"].relevance == 1.0


def test_exchange_ticker_pattern_and_alias_in_body():
    body = "Shares of Microsoft (NASDAQ: MSFT) fell.\n\nLater, Apple said it agreed."
    links = by_symbol(link_tickers("Tech stocks slide", body, SYMBOLS, INDEX))
    assert links["MSFT"].confidence == 0.9
    assert links["MSFT"].relevance == 0.7  # first paragraph
    assert links["AAPL"].confidence == 0.85
    assert links["AAPL"].relevance == 0.4  # later paragraph


def test_unknown_symbols_and_single_letter_words_are_ignored():
    links = by_symbol(link_tickers("$ZZZZ moons; F is a letter", "", SYMBOLS, INDEX))
    assert links == {}


def test_alias_requires_word_boundary():
    assert link_tickers("Pineapple prices rise", "", SYMBOLS, INDEX) == []


def test_event_classification_and_multipliers():
    assert classify_event("Apple beats Q3 earnings estimates", "") == ("earnings", 1.5)
    assert classify_event("Microsoft to acquire Activision in $69B deal", "")[0] == "mna"
    assert classify_event("FDA approves Pfizer drug", "")[0] == "regulatory"
    assert classify_event("Goldman upgrades Nvidia to buy", "")[0] == "analyst"
    assert classify_event("Why I love this stock", "") == ("other", 0.3)
