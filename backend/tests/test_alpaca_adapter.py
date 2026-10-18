import json

import httpx
import pytest

from app.services.brokers.alpaca import AlpacaAdapter, parse_trade_update
from app.services.brokers.base import OrderRequest


def make(handler):
    return AlpacaAdapter("key", "secret", paper=True, transport=httpx.MockTransport(handler))


async def test_submit_bracket_order_payload_and_headers():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "id": "abc", "client_order_id": "c1", "symbol": "AAPL", "side": "buy",
            "qty": "10", "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
            "legs": [], "updated_at": "2026-03-02T15:00:00Z",
        })

    a = make(handler)
    o = await a.submit_order(OrderRequest("AAPL", "buy", 10, limit_price=100.1,
                                          order_class="bracket", take_profit=106,
                                          stop_loss=96, client_order_id="c1"))
    assert seen["url"] == "https://paper-api.alpaca.markets/v2/orders"
    assert seen["headers"]["APCA-API-KEY-ID"] == "key"
    b = seen["body"]
    assert b["type"] == "limit" and b["limit_price"] == "100.1"
    assert b["order_class"] == "bracket"
    assert b["take_profit"] == {"limit_price": "106"}
    assert b["stop_loss"] == {"stop_price": "96"}
    assert o.broker_order_id == "abc" and o.status == "accepted" and o.qty == 10


async def test_account_positions_quote():
    def handler(req: httpx.Request):
        path = req.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"equity": "1000.5", "cash": "400", "buying_power":
                                             "800", "last_equity": "990", "daytrade_count": 2,
                                             "pattern_day_trader": False})
        if path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": "AAPL", "qty": "-3", "side": "short",
                                              "avg_entry_price": "100", "market_value": "-297"}])
        if path == "/v2/stocks/AAPL/quotes/latest":
            assert req.url.host == "data.alpaca.markets"
            return httpx.Response(200, json={"quote": {"bp": 99.0, "ap": 99.2,
                                                       "t": "2026-03-02T15:00:00.123456789Z"}})
        return httpx.Response(404)

    a = make(handler)
    acct = await a.get_account()
    assert acct.equity == 1000.5 and acct.last_equity == 990 and acct.daytrade_count == 2
    (p,) = await a.get_positions()
    assert p.qty == -3 and p.market_value == -297
    q = await a.get_quote("AAPL")
    assert q.bid == 99.0 and q.ask == 99.2 and q.ts.year == 2026


async def test_http_error_raises():
    a = make(lambda req: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(httpx.HTTPStatusError):
        await a.get_account()


async def test_bars_paginate():
    calls = []

    def handler(req: httpx.Request):
        calls.append(dict(req.url.params))
        if "page_token" not in req.url.params:
            return httpx.Response(200, json={"bars": {"AAPL": [
                {"t": "2026-01-02T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100,
                 "vw": 1.4}]}, "next_page_token": "p2"})
        return httpx.Response(200, json={"bars": {"AAPL": [
            {"t": "2026-01-05T05:00:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 120,
             "vw": 1.7}]}, "next_page_token": None})

    a = make(handler)
    bars = await a.get_bars(["AAPL"], "1Day", "2026-01-01", "2026-01-10")
    assert [b.close for b in bars["AAPL"]] == [1.5, 1.8]
    assert calls[0]["adjustment"] == "all"


async def test_news():
    def handler(req):
        return httpx.Response(200, json={"news": [{
            "id": 1, "headline": "Apple beats", "summary": "s", "content": "<p>body</p>",
            "url": "https://x.com/a", "created_at": "2026-01-02T14:00:00Z", "symbols": ["AAPL"],
            "source": "benzinga"}], "next_page_token": None})

    (item,) = await make(handler).get_news(["AAPL"], "2026-01-01", "2026-01-03")
    assert item.headline == "Apple beats" and item.body == "body" and item.symbols == ["AAPL"]


def test_parse_trade_update_fill():
    msg = {"stream": "trade_updates", "data": {
        "event": "fill", "execution_id": "e1", "qty": "10", "price": "100.5",
        "timestamp": "2026-03-02T15:00:01Z",
        "order": {"id": "abc", "client_order_id": "c1", "symbol": "AAPL", "side": "buy",
                  "qty": "10", "status": "filled", "filled_qty": "10",
                  "filled_avg_price": "100.5"}}}
    u = parse_trade_update(msg)
    assert u.event == "fill" and u.fill_id == "e1" and u.fill_qty == 10
    assert u.fill_price == 100.5 and u.order.status == "filled"
