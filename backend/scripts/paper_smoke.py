"""End-to-end check against a real Alpaca paper account, through the real API and database.

Run in CI (.github/workflows/paper.yml), never with live keys. Needs DATABASE_URL (migrated),
ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET. Steps:

1. register, add the paper account, read its balance and a SPY quote
2. rest a 1-share SPY limit buy 5% under the bid (extended hours, so it is accepted any time
   and can't fill), see it open at Alpaca, cancel it, see it land as CANCELED
3. with --fill and the market open: buy 1 SPY at market, wait for the fill, flatten with the
   kill switch, and check the round trip shows up in /trades
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.core.events import InMemoryEventBus
from app.core.locks import LocalAccountLocks
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.services.brokers.alpaca import AlpacaAdapter

SYMBOL = "SPY"
LOOSE = {"max_quote_age_s": 7 * 86400, "max_limit_deviation": 0.2, "max_position_pct": 0.5}
lines: list[str] = []


def ok(msg: str) -> None:
    lines.append(f"- ✅ {msg}")
    print("ok  ", msg, flush=True)


def fail(msg: str) -> None:
    lines.append(f"- ❌ {msg}")
    print("FAIL", msg, flush=True)
    raise SystemExit(1)


def check(r: httpx.Response, code: int, what: str) -> Any:
    if r.status_code != code:
        fail(f"{what}: HTTP {r.status_code} {r.text[:300]}")
    return r.json() if r.content else None


def failed_checks(risk: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in risk["checks"] if not c["passed"]]


async def until(what: str, probe: Callable[[], Awaitable[Any]], timeout: float = 30) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if got := await probe():
            return got
        await asyncio.sleep(1)
    fail(f"timed out after {timeout:.0f}s waiting for {what}")


async def main(fill: bool) -> None:
    key, secret = os.environ["ALPACA_PAPER_KEY"], os.environ["ALPACA_PAPER_SECRET"]
    settings = Settings(jwt_secret=secrets.token_hex(32),
                        master_key=base64.b64encode(secrets.token_bytes(32)).decode())
    engine = make_engine(os.environ["DATABASE_URL"])
    app = create_app(settings, make_sessionmaker(engine), events=InMemoryEventBus(),
                     locks=LocalAccountLocks())
    alpaca = AlpacaAdapter(key, secret, paper=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as api:
        creds = {"email": f"smoke-{secrets.token_hex(4)}@example.com",
                 "password": secrets.token_urlsafe(16)}
        check(await api.post("/auth/register", json=creds), 201, "register")
        tokens = check(await api.post("/auth/login", json=creds), 200, "login")
        api.headers["Authorization"] = f"Bearer {tokens['access_token']}"

        acct = check(await api.post("/accounts", json={
            "broker": "alpaca", "mode": "paper", "label": "smoke", "api_key": key,
            "api_secret": secret, "risk_limits": LOOSE}), 201, "add paper account")
        a = acct["id"]
        info = await alpaca.get_account()
        quote = await alpaca.get_quote(SYMBOL)
        is_open = await alpaca.is_market_open()
        ok(f"paper account: equity ${info.equity:,.2f}, buying power ${info.buying_power:,.2f}")
        ok(f"{SYMBOL} quote {quote.bid:.2f} / {quote.ask:.2f}; market "
           f"{'open' if is_open else 'closed'}")

        async def order(oid: str) -> dict[str, Any] | None:
            rows = check(await api.get(f"/accounts/{a}/orders", params={"status": "all"}),
                         200, "list orders")
            return next((o for o in rows if o["id"] == oid), None)

        async def reconciled(oid: str, status: str) -> dict[str, Any] | None:
            check(await api.post(f"/accounts/{a}/reconcile"), 200, "reconcile")
            o = await order(oid)
            return o if o and o["status"] == status else None

        # 2. a resting order that can't fill, then cancel it
        limit = round(quote.bid * 0.95, 2)
        out = check(await api.post(f"/accounts/{a}/orders", json={
            "symbol": SYMBOL, "side": "buy", "qty": 1, "order_type": "limit",
            "limit_price": limit, "extended_hours": True}), 200, "place resting order")
        if out["order"] is None:
            fail(f"risk rejected the resting order: {failed_checks(out['risk'])}")
        rest = out["order"]
        if not rest["broker_order_id"]:
            fail(f"resting order has no broker id: {rest}")
        ok(f"resting buy 1 {SYMBOL} @ {limit} sent, Alpaca id {rest['broker_order_id']}")
        recent = await alpaca.list_orders(datetime.now(UTC) - timedelta(hours=1))
        at_broker = {o.broker_order_id: o for o in recent}
        if rest["broker_order_id"] not in at_broker:
            fail("Alpaca doesn't list the resting order")
        ok(f"Alpaca lists it as {at_broker[rest['broker_order_id']].status}")
        check(await api.post(f"/orders/{rest['id']}/cancel"), 202, "cancel")
        await until("the cancel to reconcile", lambda: reconciled(rest["id"], "CANCELED"))
        ok("cancel confirmed by reconcile: CANCELED")

        # 3. a real fill and a flatten, only while the market is open
        if not fill:
            ok("fill step skipped (run with fill=true during market hours to include it)")
        elif not is_open:
            ok("fill step skipped: market closed (09:30-16:00 ET, Mon-Fri)")
        else:
            out = check(await api.post(f"/accounts/{a}/orders", json={
                "symbol": SYMBOL, "side": "buy", "qty": 1, "order_type": "market"}),
                200, "place market order")
            if out["order"] is None:
                fail(f"risk rejected the market order: {failed_checks(out['risk'])}")
            filled = await until("the fill", lambda: reconciled(out["order"]["id"], "FILLED"))
            ok(f"bought 1 {SYMBOL}, filled at {filled['filled_avg_price']}")
            check(await api.post(f"/accounts/{a}/kill", json={"flatten": True}), 200, "kill")

            async def flat() -> bool:
                return not any(p.symbol == SYMBOL for p in await alpaca.get_positions())

            await until("the position to close", flat, timeout=60)
            ok("kill switch flattened the position")

            async def round_trip() -> list[dict[str, Any]] | None:
                check(await api.post(f"/accounts/{a}/reconcile"), 200, "reconcile")
                rows = check(await api.get(f"/accounts/{a}/trades"), 200, "trades")
                return [t for t in rows if t["symbol"] == SYMBOL] or None

            (trade, *_) = await until("the round trip in /trades", round_trip, timeout=60)
            ok(f"/trades shows the round trip: {trade['direction']} {trade['qty']:g} {SYMBOL}")

    await alpaca.aclose()
    await engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--fill", action="store_true", help="also buy and flatten 1 share if open")
    args = p.parse_args()
    try:
        asyncio.run(main(args.fill))
    finally:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write("## Paper trading smoke run\n\n" + "\n".join(lines) + "\n")
