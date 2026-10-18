"""Alpaca trading, market data and news over REST, plus the trade_updates WebSocket."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from app.services.brokers.base import (
    Account,
    BrokerOrder,
    NewsArticle,
    OrderRequest,
    Position,
    PriceBar,
    TradeUpdate,
)
from app.services.risk.manager import Quote

log = logging.getLogger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
_FRACTION = re.compile(r"(\.\d{6})\d+")
_TAG = re.compile(r"<[^>]+>")


def parse_ts(s: str) -> datetime:
    """Alpaca timestamps can carry nanoseconds; Python parses at most microseconds."""
    return datetime.fromisoformat(_FRACTION.sub(r"\1", s).replace("Z", "+00:00"))


def _f(x: Any, default: float = 0.0) -> float:
    return default if x is None or x == "" else float(x)


def price_str(p: float) -> str:
    """Alpaca accepts 2 decimals at or above $1 and 4 below."""
    p = round(p, 2) if p >= 1 else round(p, 4)
    return f"{p:.4f}".rstrip("0").rstrip(".")


def _order(d: dict[str, Any]) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=d["id"],
        client_order_id=d.get("client_order_id", ""),
        symbol=d["symbol"],
        side=d["side"],
        qty=_f(d.get("qty")),
        status=d["status"],
        filled_qty=_f(d.get("filled_qty")),
        filled_avg_price=_f(d["filled_avg_price"]) if d.get("filled_avg_price") else None,
        legs=[_order(leg) for leg in d.get("legs") or []],
        updated_at=parse_ts(d["updated_at"]) if d.get("updated_at") else None,
    )


def parse_trade_update(msg: dict[str, Any]) -> TradeUpdate:
    d = msg["data"]
    return TradeUpdate(
        event=d["event"],
        order=_order(d["order"]),
        fill_id=d.get("execution_id"),
        fill_qty=_f(d["qty"]) if d.get("qty") else None,
        fill_price=_f(d["price"]) if d.get("price") else None,
        at=parse_ts(d["timestamp"]) if d.get("timestamp") else None,
    )


class AlpacaAdapter:
    def __init__(
        self,
        key_id: str,
        secret: str,
        paper: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        data_feed: str = "iex",
    ):
        self.key_id = key_id
        self.secret = secret
        self.base_url = PAPER_URL if paper else LIVE_URL
        self.data_feed = data_feed
        headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self._http = httpx.AsyncClient(headers=headers, transport=transport, timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, url: str, **kw: Any) -> Any:
        r = await self._http.request(method, url, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    async def _t(self, method: str, path: str, **kw: Any) -> Any:
        return await self._req(method, self.base_url + path, **kw)

    async def _d(self, path: str, **kw: Any) -> Any:
        return await self._req("GET", DATA_URL + path, **kw)

    # trading
    async def get_account(self) -> Account:
        d = await self._t("GET", "/v2/account")
        return Account(
            equity=_f(d["equity"]),
            cash=_f(d["cash"]),
            buying_power=_f(d["buying_power"]),
            last_equity=_f(d.get("last_equity"), _f(d["equity"])),
            pattern_day_trader=bool(d.get("pattern_day_trader")),
            daytrade_count=int(d.get("daytrade_count") or 0),
        )

    async def get_positions(self) -> list[Position]:
        return [
            Position(p["symbol"], _f(p["qty"]), _f(p["avg_entry_price"]), _f(p["market_value"]))
            for p in await self._t("GET", "/v2/positions")
        ]

    async def get_quote(self, symbol: str) -> Quote:
        d = await self._d(f"/v2/stocks/{symbol}/quotes/latest", params={"feed": self.data_feed})
        q = d["quote"]
        return Quote(bid=_f(q["bp"]), ask=_f(q["ap"]), ts=parse_ts(q["t"]))

    async def submit_order(self, req: OrderRequest) -> BrokerOrder:
        body: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side,
            "qty": f"{req.qty:g}",
            "type": req.order_type,
            "time_in_force": req.time_in_force,
        }
        if req.limit_price is not None:
            body["limit_price"] = price_str(req.limit_price)
        if req.stop_price is not None:
            body["stop_price"] = price_str(req.stop_price)
        if req.client_order_id:
            body["client_order_id"] = req.client_order_id
        if req.extended_hours:
            body["extended_hours"] = True
        if req.order_class != "simple":
            body["order_class"] = req.order_class
            if req.take_profit is not None:
                body["take_profit"] = {"limit_price": price_str(req.take_profit)}
            if req.stop_loss is not None:
                body["stop_loss"] = {"stop_price": price_str(req.stop_loss)}
        return _order(await self._t("POST", "/v2/orders", json=body))

    async def cancel_order(self, broker_order_id: str) -> None:
        await self._t("DELETE", f"/v2/orders/{broker_order_id}")

    async def cancel_all(self) -> None:
        await self._t("DELETE", "/v2/orders")

    async def close_all_positions(self) -> None:
        await self._t("DELETE", "/v2/positions", params={"cancel_orders": "true"})

    async def list_orders(self, since: datetime) -> list[BrokerOrder]:
        params = {"status": "all", "after": since.isoformat(), "limit": 500, "nested": "true"}
        return [_order(o) for o in await self._t("GET", "/v2/orders", params=params)]

    async def is_market_open(self) -> bool:
        return bool((await self._t("GET", "/v2/clock"))["is_open"])

    async def asset_flags(self, symbol: str) -> tuple[bool, bool]:
        d = await self._t("GET", f"/v2/assets/{symbol}")
        return bool(d.get("tradable")), bool(d.get("shortable") and d.get("easy_to_borrow"))

    # market data and news
    async def get_bars(
        self, symbols: list[str], timeframe: str, start: str, end: str
    ) -> dict[str, list[PriceBar]]:
        out: dict[str, list[PriceBar]] = {s: [] for s in symbols}
        params: dict[str, Any] = {
            "symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end,
            "adjustment": "all", "limit": 10000, "feed": self.data_feed,
        }
        while True:
            d = await self._d("/v2/stocks/bars", params=params)
            for sym, rows in (d.get("bars") or {}).items():
                out.setdefault(sym, []).extend(
                    PriceBar(parse_ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw"))
                    for b in rows
                )
            token = d.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    async def get_news(
        self, symbols: list[str], start: str, end: str, limit: int = 50
    ) -> list[NewsArticle]:
        out: list[NewsArticle] = []
        params: dict[str, Any] = {
            "symbols": ",".join(symbols), "start": start, "end": end, "limit": limit,
            "include_content": "true", "sort": "asc",
        }
        while True:
            d = await self._d("/v1beta1/news", params=params)
            for n in d.get("news") or []:
                body = html.unescape(_TAG.sub(" ", n.get("content") or "")).strip()
                out.append(NewsArticle(
                    id=str(n["id"]), headline=n["headline"], summary=n.get("summary") or "",
                    body=re.sub(r"\s+", " ", body), url=n.get("url") or "",
                    created_at=parse_ts(n["created_at"]), symbols=list(n.get("symbols") or []),
                    source=n.get("source") or "alpaca",
                ))
            token = d.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    # streaming
    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]:
        import websockets

        url = self.base_url.replace("https://", "wss://") + "/stream"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url) as ws:
                    await ws.send(json.dumps(
                        {"action": "auth", "key": self.key_id, "secret": self.secret}
                    ))
                    await ws.send(json.dumps(
                        {"action": "listen", "data": {"streams": ["trade_updates"]}}
                    ))
                    backoff = 1.0
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("stream") == "trade_updates":
                            yield parse_trade_update(msg)
            except Exception:
                log.exception("trade stream dropped; reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
