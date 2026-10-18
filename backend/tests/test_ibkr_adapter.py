import json

import httpx
import pytest

from app.services.brokers.base import OrderRequest
from app.services.brokers.ibkr import IbkrAdapter

ACCT = "U1234567"


class Gateway:
    """A fake Client Portal gateway that records what it was sent."""

    def __init__(self):
        self.sent: list[tuple[str, str, object]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/v1/api")
        body = json.loads(req.content) if req.content else None
        self.sent.append((req.method, path, body))
        if path == "/iserver/secdef/search":
            sym = req.url.params["symbol"]
            return httpx.Response(200, json=[] if sym == "NOPE" else [{"conid": 265598, "symbol": sym}])
        if path == f"/portfolio/{ACCT}/summary":
            return httpx.Response(200, json={
                "netliquidation": {"amount": 105000.0}, "totalcashvalue": {"amount": 40000.0},
                "buyingpower": {"amount": 160000.0},
                "previousdayequitywithloanvalue": {"amount": 104000.0}})
        if path == f"/portfolio/{ACCT}/positions/0":
            return httpx.Response(200, json=[{"conid": 265598, "contractDesc": "AAPL",
                                              "position": 50, "avgCost": 180.0, "mktValue": 9500.0}])
        if path == "/iserver/marketdata/snapshot":
            return httpx.Response(200, json=[{"conid": 265598, "84": "189.95", "86": "190.05"}])
        if path == f"/iserver/account/{ACCT}/orders":
            return httpx.Response(200, json=[{"id": "reply-1", "message": ["price warning"]}])
        if path == "/iserver/reply/reply-1":
            return httpx.Response(200, json=[{"order_id": "987", "order_status": "Submitted"}])
        if path == "/iserver/account/orders":
            return httpx.Response(200, json={"orders": [
                {"orderId": 987, "order_ref": "coid-1", "ticker": "AAPL", "side": "BUY",
                 "totalSize": 10, "filledQuantity": 4, "avgPrice": "190.01", "status": "Submitted",
                 "lastExecutionTime_r": 1772460000000},
                {"orderId": 988, "order_ref": "coid-2", "ticker": "AAPL", "side": "SELL",
                 "totalSize": 5, "filledQuantity": 5, "avgPrice": "191", "status": "Filled",
                 "lastExecutionTime_r": 1772460000000},
            ]})
        if req.method == "DELETE":
            return httpx.Response(200, json={"msg": "Request was submitted"})
        return httpx.Response(404, json={"error": path})


@pytest.fixture
def gw():
    return Gateway()


@pytest.fixture
async def ib(gw):
    a = IbkrAdapter(ACCT, "https://localhost:5000", transport=httpx.MockTransport(gw))
    yield a
    await a.aclose()


async def test_account_positions_quote(ib):
    acct = await ib.get_account()
    assert (acct.equity, acct.cash, acct.buying_power, acct.last_equity) == (105000, 40000, 160000, 104000)
    (p,) = await ib.get_positions()
    assert (p.symbol, p.qty, p.avg_entry_price, p.market_value) == ("AAPL", 50, 180.0, 9500.0)
    q = await ib.get_quote("AAPL")
    assert (q.bid, q.ask) == (189.95, 190.05)
    assert await ib.asset_flags("AAPL") == (True, True)
    assert await ib.asset_flags("NOPE") == (False, False)


async def test_submit_confirms_warnings_and_sends_bracket_children(ib, gw):
    order = await ib.submit_order(OrderRequest(
        "AAPL", "buy", 10, "limit", limit_price=190.0, order_class="bracket",
        take_profit=200.0, stop_loss=185.0, client_order_id="coid-1"))
    assert order.broker_order_id == "987" and order.client_order_id == "coid-1"
    _, _, body = next(s for s in gw.sent if s[1] == f"/iserver/account/{ACCT}/orders")
    parent, tp, sl = body["orders"]
    assert parent == {"conid": 265598, "orderType": "LMT", "side": "BUY", "quantity": 10,
                      "price": 190.0, "tif": "DAY", "cOID": "coid-1", "outsideRTH": False}
    assert (tp["orderType"], tp["side"], tp["price"], tp["parentId"]) == ("LMT", "SELL", 200.0, "coid-1")
    assert (sl["orderType"], sl["side"], sl["price"], sl["parentId"]) == ("STP", "SELL", 185.0, "coid-1")
    assert ("POST", "/iserver/reply/reply-1", {"confirmed": True}) in gw.sent


async def test_list_orders_maps_status(ib):
    from datetime import UTC, datetime

    orders = {o.broker_order_id: o for o in await ib.list_orders(datetime(2026, 1, 1, tzinfo=UTC))}
    assert orders["987"].status == "partially_filled" and orders["987"].filled_qty == 4
    assert orders["987"].filled_avg_price == 190.01 and orders["987"].client_order_id == "coid-1"
    assert orders["988"].status == "filled" and orders["988"].side == "sell"


async def test_cancel_all_cancels_open_orders_only(ib, gw):
    await ib.cancel_all()
    deletes = [p for m, p, _ in gw.sent if m == "DELETE"]
    assert deletes == [f"/iserver/account/{ACCT}/order/987"]


def test_gateway_tls_verification_only_relaxed_for_localhost():
    assert IbkrAdapter(ACCT, "https://localhost:5000")._verify is False
    assert IbkrAdapter(ACCT, "https://ib.example.com")._verify is True
