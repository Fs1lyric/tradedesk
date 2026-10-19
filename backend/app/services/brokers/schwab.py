"""Charles Schwab Trader API over REST.

Schwab's OAuth is three-legged; the user does the browser login once and stores the app key,
app secret and refresh token. The adapter swaps the refresh token for 30-minute access tokens
and retries a request once on a 401. Trade updates come from polling the order list.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.services.brokers.base import Account, BrokerOrder, OrderRequest, Position, TradeUpdate
from app.services.brokers.polling import poll_trade_updates
from app.services.risk.manager import Quote

BASE_URL = "https://api.schwabapi.com"
ORDER_TYPES = {"market": "MARKET", "limit": "LIMIT", "stop": "STOP", "stop_limit": "STOP_LIMIT"}
DURATION = {"day": "DAY", "gtc": "GOOD_TILL_CANCEL", "ioc": "IMMEDIATE_OR_CANCEL",
            "fok": "FILL_OR_KILL"}
STATUS = {"FILLED": "filled", "CANCELED": "canceled", "REJECTED": "rejected",
          "EXPIRED": "expired", "PENDING_CANCEL": "pending_cancel", "REPLACED": "replaced",
          "PENDING_REPLACE": "pending_replace", "AWAITING_PARENT_ORDER": "held"}
DONE = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"}


def parse_credentials(secret: str) -> tuple[str, str, str]:
    """The account's api_secret is a JSON object with app_key, app_secret and refresh_token."""
    try:
        d = json.loads(secret)
        return str(d["app_key"]), str(d["app_secret"]), str(d["refresh_token"])
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(
            "schwab secret must be JSON with app_key, app_secret and refresh_token") from e


def price_str(p: float) -> str:
    return f"{p:.2f}" if p >= 1 else f"{p:.4f}"


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def _instruction(side: str, position: float) -> str:
    if side == "buy":
        return "BUY_TO_COVER" if position < 0 else "BUY"
    return "SELL" if position > 0 else "SELL_SHORT"


def _order(d: dict[str, Any]) -> BrokerOrder:
    leg = d["orderLegCollection"][0]
    activity = d.get("orderActivityCollection") or []
    execs = [x for a in activity for x in a.get("executionLegs") or []]
    filled = float(d.get("filledQuantity") or 0)
    qty = float(d.get("quantity") or 0)
    exec_qty = sum(float(x["quantity"]) for x in execs)
    notional = sum(float(x["quantity"]) * float(x["price"]) for x in execs)
    avg = notional / exec_qty if exec_qty else None
    raw = str(d.get("status", ""))
    status = STATUS.get(raw) or ("partially_filled" if 0 < filled < qty else "accepted")
    when = d.get("closeTime") or d.get("enteredTime")
    return BrokerOrder(
        broker_order_id=str(d["orderId"]), client_order_id="",
        symbol=leg["instrument"]["symbol"],
        side="buy" if leg["instruction"].startswith("BUY") else "sell",
        qty=qty, status=status, filled_qty=filled, filled_avg_price=avg,
        updated_at=_ts(when) if when else None,
    )


class SchwabAdapter:
    def __init__(
        self, account_hash: str, app_key: str, app_secret: str, refresh_token: str,
        transport: httpx.AsyncBaseTransport | None = None, poll_interval: float = 2.0,
    ):
        self.account_hash = account_hash
        self.refresh_token = refresh_token
        self.poll_interval = poll_interval
        self._basic = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
        self._token: str | None = None
        self._expires = 0.0
        self._http = httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _refresh(self) -> str:
        r = await self._http.post(
            "/v1/oauth/token", headers={"Authorization": f"Basic {self._basic}"},
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        r.raise_for_status()
        d = r.json()
        self._token = str(d["access_token"])
        self._expires = time.monotonic() + float(d.get("expires_in", 1800)) - 60
        return self._token

    async def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        token = self._token if self._token and time.monotonic() < self._expires else None
        for attempt in range(2):
            token = token or await self._refresh()
            r = await self._http.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
            if r.status_code != 401 or attempt:
                r.raise_for_status()
                return r
            token = None
        raise AssertionError("unreachable")

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._send(method, path, **kw)
        return r.json() if r.content else None

    @property
    def _acct(self) -> str:
        return f"/trader/v1/accounts/{self.account_hash}"

    async def _securities(self) -> dict[str, Any]:
        d = await self._json("GET", self._acct, params={"fields": "positions"})
        return dict(d["securitiesAccount"])

    async def get_account(self) -> Account:
        d = await self._securities()
        cur = d.get("currentBalances") or {}
        equity = float(cur.get("liquidationValue") or 0)
        start = (d.get("initialBalances") or {}).get("liquidationValue")
        return Account(equity=equity, cash=float(cur.get("cashBalance") or 0),
                       buying_power=float(cur.get("buyingPower") or 0),
                       last_equity=float(start) if start is not None else equity)

    async def get_positions(self) -> list[Position]:
        out = []
        for p in (await self._securities()).get("positions") or []:
            qty = float(p.get("longQuantity") or 0) - float(p.get("shortQuantity") or 0)
            if qty:
                out.append(Position(p["instrument"]["symbol"], qty, float(p["averagePrice"]),
                                    float(p["marketValue"])))
        return out

    async def get_quote(self, symbol: str) -> Quote:
        d = await self._json("GET", "/marketdata/v1/quotes",
                             params={"symbols": symbol, "fields": "quote"})
        q = d[symbol]["quote"]
        return Quote(bid=float(q["bidPrice"]), ask=float(q["askPrice"]),
                     ts=datetime.fromtimestamp(q["quoteTime"] / 1000, UTC))

    def _single(self, symbol: str, instruction: str, qty: float, order_type: str,
                price: float | None, stop: float | None, req: OrderRequest) -> dict[str, Any]:
        o: dict[str, Any] = {
            "session": "SEAMLESS" if req.extended_hours else "NORMAL",
            "duration": DURATION.get(req.time_in_force, "DAY"),
            "orderType": order_type,
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [{"instruction": instruction, "quantity": qty,
                                    "instrument": {"symbol": symbol, "assetType": "EQUITY"}}],
        }
        if price is not None:
            o["price"] = price_str(price)
        if stop is not None:
            o["stopPrice"] = price_str(stop)
        return o

    async def submit_order(self, req: OrderRequest) -> BrokerOrder:
        held = {p.symbol: p.qty for p in await self.get_positions()}.get(req.symbol, 0.0)
        signed = req.qty if req.side == "buy" else -req.qty
        exit_side = "sell" if req.side == "buy" else "buy"
        exit_instr = _instruction(exit_side, held + signed)
        exits = []
        if req.take_profit is not None:
            exits.append(self._single(req.symbol, exit_instr, req.qty, "LIMIT",
                                      req.take_profit, None, req))
        if req.stop_loss is not None:
            exits.append(self._single(req.symbol, exit_instr, req.qty, "STOP",
                                      None, req.stop_loss, req))
        if req.order_class == "oco":
            body: dict[str, Any] = {"orderStrategyType": "OCO", "childOrderStrategies": exits}
        else:
            body = self._single(req.symbol, _instruction(req.side, held), req.qty,
                                ORDER_TYPES[req.order_type], req.limit_price, req.stop_price, req)
            if req.order_class == "bracket" and exits:
                body["orderStrategyType"] = "TRIGGER"
                body["childOrderStrategies"] = [
                    {"orderStrategyType": "OCO", "childOrderStrategies": exits}]
        r = await self._send("POST", f"{self._acct}/orders", json=body)
        order_id = r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        return BrokerOrder(order_id, req.client_order_id or "", req.symbol, req.side, req.qty,
                           "accepted")

    async def cancel_order(self, broker_order_id: str) -> None:
        await self._send("DELETE", f"{self._acct}/orders/{broker_order_id}")

    async def _raw_orders(self, since: datetime) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        params = {"fromEnteredTime": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                  "toEnteredTime": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        return list(await self._json("GET", f"{self._acct}/orders", params=params) or [])

    async def cancel_all(self) -> None:
        for o in await self._raw_orders(datetime.now(UTC) - timedelta(days=60)):
            if o.get("status") not in DONE:
                await self.cancel_order(str(o["orderId"]))

    async def close_all_positions(self) -> None:
        await self.cancel_all()
        for p in await self.get_positions():
            await self.submit_order(OrderRequest(
                p.symbol, "sell" if p.qty > 0 else "buy", abs(p.qty), "market"))

    async def list_orders(self, since: datetime) -> list[BrokerOrder]:
        return [_order(o) for o in await self._raw_orders(since)]

    async def is_market_open(self) -> bool:
        d = await self._json("GET", "/marketdata/v1/markets", params={"markets": "equity"})
        return bool(((d.get("equity") or {}).get("EQ") or {}).get("isOpen"))

    async def asset_flags(self, symbol: str) -> tuple[bool, bool]:
        # Schwab doesn't publish borrow availability; a short it can't fill is rejected there.
        d = await self._json("GET", "/marketdata/v1/instruments",
                             params={"symbol": symbol, "projection": "symbol-search"})
        found = bool((d or {}).get("instruments"))
        return found, found

    def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]:
        return poll_trade_updates(self.list_orders, self.poll_interval)
