"""Interactive Brokers through the Client Portal gateway's REST API.

The gateway runs next to the app (usually https://localhost:5000) and holds the logged-in
session, so the adapter only needs the account id and the gateway URL. There is no trade
stream; updates come from polling the order list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.brokers.base import Account, BrokerOrder, OrderRequest, Position, TradeUpdate
from app.services.brokers.polling import poll_trade_updates, us_market_open
from app.services.risk.manager import Quote

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ORDER_TYPES = {"market": "MKT", "limit": "LMT", "stop": "STP", "stop_limit": "STOP_LIMIT"}
TIF = {"day": "DAY", "gtc": "GTC", "ioc": "IOC", "opg": "OPG"}
BID, ASK = "84", "86"
OPEN = {"pendingsubmit", "presubmitted", "submitted", "pendingcancel"}


def _f(x: Any, default: float = 0.0) -> float:
    return default if x is None or x == "" else float(x)


def _amount(summary: dict[str, Any], key: str) -> float:
    return _f((summary.get(key) or {}).get("amount"))


def _status(raw: str, filled: float, total: float) -> str:
    s = raw.lower()
    if s == "filled":
        return "filled"
    if s in {"cancelled", "apicancelled"}:
        return "canceled"
    if s == "inactive":
        return "rejected"
    if s == "pendingcancel":
        return "pending_cancel"
    if s in {"pendingsubmit", "presubmitted"}:
        return "pending_new"
    return "partially_filled" if 0 < filled < total else "accepted"


def _order(d: dict[str, Any]) -> BrokerOrder:
    filled, total = _f(d.get("filledQuantity")), _f(d.get("totalSize"))
    ts = d.get("lastExecutionTime_r")
    return BrokerOrder(
        broker_order_id=str(d["orderId"]),
        client_order_id=d.get("order_ref") or "",
        symbol=d.get("ticker") or "",
        side=str(d.get("side", "")).lower(),
        qty=total,
        status=_status(str(d.get("status", "")), filled, total),
        filled_qty=filled,
        filled_avg_price=_f(d["avgPrice"]) if d.get("avgPrice") else None,
        updated_at=datetime.fromtimestamp(ts / 1000, UTC) if ts else None,
    )


class IbkrAdapter:
    def __init__(
        self, account_id: str, gateway_url: str,
        transport: httpx.AsyncBaseTransport | None = None, poll_interval: float = 2.0,
    ):
        self.account_id = account_id
        self.base_url = gateway_url.rstrip("/") + "/v1/api"
        # The gateway ships a self-signed certificate; trust it only on this machine.
        self._verify = urlparse(gateway_url).hostname not in LOCAL_HOSTS
        self.poll_interval = poll_interval
        self._conids: dict[str, int | None] = {}
        self._http = httpx.AsyncClient(transport=transport, verify=self._verify, timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._http.request(method, self.base_url + path, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    async def _conid(self, symbol: str) -> int | None:
        if symbol not in self._conids:
            hits = await self._req("GET", "/iserver/secdef/search", params={"symbol": symbol})
            self._conids[symbol] = int(hits[0]["conid"]) if hits else None
        return self._conids[symbol]

    async def _require_conid(self, symbol: str) -> int:
        conid = await self._conid(symbol)
        if conid is None:
            raise ValueError(f"no IBKR contract for {symbol}")
        return conid

    async def get_account(self) -> Account:
        d = await self._req("GET", f"/portfolio/{self.account_id}/summary")
        equity = _amount(d, "netliquidation")
        return Account(
            equity=equity, cash=_amount(d, "totalcashvalue"),
            buying_power=_amount(d, "buyingpower"),
            last_equity=_amount(d, "previousdayequitywithloanvalue") or equity,
        )

    async def get_positions(self) -> list[Position]:
        rows = await self._req("GET", f"/portfolio/{self.account_id}/positions/0") or []
        return [
            Position(p["contractDesc"], _f(p["position"]), _f(p["avgCost"]), _f(p["mktValue"]))
            for p in rows if _f(p.get("position"))
        ]

    async def get_quote(self, symbol: str) -> Quote:
        conid = await self._require_conid(symbol)
        (d,) = await self._req("GET", "/iserver/marketdata/snapshot",
                               params={"conids": str(conid), "fields": f"{BID},{ASK}"})
        return Quote(bid=_f(d.get(BID)), ask=_f(d.get(ASK)), ts=datetime.now(UTC))

    def _leg(self, conid: int, req: OrderRequest, side: str, order_type: str,
             price: float | None, **extra: Any) -> dict[str, Any]:
        leg: dict[str, Any] = {"conid": conid, "orderType": order_type, "side": side.upper(),
                               "quantity": req.qty}
        if price is not None:
            leg["price"] = price
        leg["tif"] = TIF.get(req.time_in_force, "DAY")
        return leg | extra

    async def submit_order(self, req: OrderRequest) -> BrokerOrder:
        conid = await self._require_conid(req.symbol)
        coid = req.client_order_id or ""
        price = req.stop_price if req.order_type == "stop" else req.limit_price
        parent = self._leg(conid, req, req.side, ORDER_TYPES[req.order_type], price,
                           cOID=coid, outsideRTH=req.extended_hours)
        if req.order_type == "stop_limit":
            parent["auxPrice"] = req.stop_price
        exit_side = "sell" if req.side == "buy" else "buy"
        orders = [parent]
        if req.order_class in {"bracket", "oco"}:
            if req.take_profit is not None:
                orders.append(self._leg(conid, req, exit_side, "LMT", req.take_profit,
                                        parentId=coid, cOID=f"{coid}-tp"))
            if req.stop_loss is not None:
                orders.append(self._leg(conid, req, exit_side, "STP", req.stop_loss,
                                        parentId=coid, cOID=f"{coid}-sl"))
        replies = await self._req("POST", f"/iserver/account/{self.account_id}/orders",
                                  json={"orders": orders})
        # The gateway answers warnings ("price exceeds ...") with a question to confirm.
        for _ in range(5):
            first = replies[0]
            if "order_id" in first:
                return BrokerOrder(
                    broker_order_id=str(first["order_id"]), client_order_id=coid,
                    symbol=req.symbol, side=req.side, qty=req.qty,
                    status=_status(first.get("order_status", ""), 0, req.qty),
                )
            if "id" not in first:
                raise RuntimeError(f"IBKR order rejected: {first}")
            replies = await self._req("POST", f"/iserver/reply/{first['id']}",
                                      json={"confirmed": True})
        raise RuntimeError("IBKR kept asking for confirmation")

    async def cancel_order(self, broker_order_id: str) -> None:
        await self._req("DELETE", f"/iserver/account/{self.account_id}/order/{broker_order_id}")

    async def _raw_orders(self) -> list[dict[str, Any]]:
        d = await self._req("GET", "/iserver/account/orders")
        return list((d or {}).get("orders") or [])

    async def cancel_all(self) -> None:
        for o in await self._raw_orders():
            if str(o.get("status", "")).lower() in OPEN:
                await self.cancel_order(str(o["orderId"]))

    async def close_all_positions(self) -> None:
        await self.cancel_all()
        for p in await self.get_positions():
            await self.submit_order(OrderRequest(
                p.symbol, "sell" if p.qty > 0 else "buy", abs(p.qty), "market"))

    async def list_orders(self, since: datetime) -> list[BrokerOrder]:
        orders = [_order(o) for o in await self._raw_orders()]
        return [o for o in orders if o.updated_at is None or o.updated_at >= since]

    async def is_market_open(self) -> bool:
        return us_market_open(datetime.now(UTC))

    async def asset_flags(self, symbol: str) -> tuple[bool, bool]:
        # Shortability needs a separate borrow lookup; the order is rejected at IBKR if not.
        found = await self._conid(symbol) is not None
        return found, found

    def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]:
        return poll_trade_updates(self.list_orders, self.poll_interval)
