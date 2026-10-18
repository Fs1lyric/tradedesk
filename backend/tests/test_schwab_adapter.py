import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.services.brokers.base import OrderRequest
from app.services.brokers.schwab import SchwabAdapter, parse_credentials

HASH = "ABC123HASH"


class Api:
    def __init__(self):
        self.sent: list[tuple[str, str, object]] = []
        self.tokens = 0
        self.expire_next = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = req.content.decode() if req.content else None
        self.sent.append((req.method, path, body))
        if path == "/v1/oauth/token":
            assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"key:secret").decode()
            assert "grant_type=refresh_token" in body and "refresh_token=rt" in body
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"at{self.tokens}", "expires_in": 1800})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"message": "expired"})
        assert req.headers["Authorization"].startswith("Bearer at")
        if path == f"/trader/v1/accounts/{HASH}":
            return httpx.Response(200, json={"securitiesAccount": {
                "currentBalances": {"liquidationValue": 52000.0, "cashBalance": 12000.0,
                                    "buyingPower": 24000.0},
                "initialBalances": {"liquidationValue": 51000.0},
                "positions": [{"instrument": {"symbol": "MSFT"}, "longQuantity": 0,
                               "shortQuantity": 20, "averagePrice": 410.0, "marketValue": -8100.0}],
            }})
        if path == "/marketdata/v1/quotes":
            return httpx.Response(200, json={"MSFT": {"quote": {
                "bidPrice": 404.9, "askPrice": 405.1, "quoteTime": 1772460000000}}})
        if path == f"/trader/v1/accounts/{HASH}/orders" and req.method == "POST":
            return httpx.Response(201, headers={
                "Location": f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/orders/5550001"})
        if path == f"/trader/v1/accounts/{HASH}/orders":
            return httpx.Response(200, json=[{
                "orderId": 5550001, "status": "WORKING", "quantity": 20, "filledQuantity": 5,
                "orderLegCollection": [{"instruction": "BUY_TO_COVER",
                                        "instrument": {"symbol": "MSFT"}}],
                "orderActivityCollection": [{"executionLegs": [
                    {"quantity": 2, "price": 405.0}, {"quantity": 3, "price": 406.0}]}],
                "enteredTime": "2026-03-02T15:00:00+0000",
            }])
        if path == "/marketdata/v1/markets":
            return httpx.Response(200, json={"equity": {"EQ": {"isOpen": True}}})
        if req.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404, json={"path": path})


@pytest.fixture
def server():
    return Api()


@pytest.fixture
async def sw(server):
    a = SchwabAdapter(HASH, "key", "secret", "rt", transport=httpx.MockTransport(server))
    yield a
    await a.aclose()


def test_parse_credentials():
    assert parse_credentials('{"app_key": "k", "app_secret": "s", "refresh_token": "r"}') == ("k", "s", "r")
    with pytest.raises(ValueError):
        parse_credentials('{"app_key": "k"}')


async def test_account_positions_quote_and_token_refresh(sw, server):
    acct = await sw.get_account()
    assert (acct.equity, acct.cash, acct.buying_power, acct.last_equity) == (52000, 12000, 24000, 51000)
    (p,) = await sw.get_positions()
    assert (p.symbol, p.qty) == ("MSFT", -20)
    server.expire_next = True  # a 401 refreshes the token once and retries
    q = await sw.get_quote("MSFT")
    assert (q.bid, q.ask) == (404.9, 405.1)
    assert server.tokens == 2
    assert await sw.is_market_open() is True


async def test_buy_against_a_short_is_a_cover_bracket(sw, server):
    order = await sw.submit_order(OrderRequest(
        "MSFT", "buy", 20, "limit", limit_price=405.25, order_class="bracket",
        take_profit=390.0, stop_loss=415.0, client_order_id="coid-9"))
    assert order.broker_order_id == "5550001" and order.client_order_id == "coid-9"
    body = json.loads(next(b for m, p, b in server.sent if m == "POST" and p.endswith("/orders")))
    assert body["orderStrategyType"] == "TRIGGER"
    assert body["price"] == "405.25" and body["orderType"] == "LIMIT"
    assert body["orderLegCollection"][0]["instruction"] == "BUY_TO_COVER"
    (oco,) = body["childOrderStrategies"]
    tp, sl = oco["childOrderStrategies"]
    assert (tp["orderType"], tp["price"]) == ("LIMIT", "390.00")
    assert (sl["orderType"], sl["stopPrice"]) == ("STOP", "415.00")
    assert tp["orderLegCollection"][0]["instruction"] == "SELL_SHORT"


async def test_list_orders(sw):
    (o,) = await sw.list_orders(datetime(2026, 3, 1, tzinfo=UTC))
    assert o.broker_order_id == "5550001" and o.side == "buy" and o.symbol == "MSFT"
    assert o.status == "partially_filled" and o.filled_qty == 5
    assert o.filled_avg_price == pytest.approx(405.6)
