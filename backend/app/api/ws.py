"""Live order updates over a WebSocket. Browsers can't set headers on a WebSocket, so the
access token comes in the query string."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket
from sqlalchemy import select

from app.core.events import EventBus, order_channel
from app.core.security import TokenError, decode_token
from app.models import BrokerAccount, User

router = APIRouter(tags=["ws"])

UNAUTHORIZED = 4401


@router.websocket("/ws/orders")
async def order_updates(ws: WebSocket, token: str = "") -> None:
    app = ws.app
    try:
        user_id = decode_token(token, app.state.settings, "access")["sub"]
    except TokenError:
        await ws.close(code=UNAUTHORIZED)
        return
    async with app.state.sessions() as s:
        if await s.get(User, user_id) is None:
            await ws.close(code=UNAUTHORIZED)
            return
        accounts = list(await s.scalars(
            select(BrokerAccount.id).where(BrokerAccount.user_id == user_id)
            .order_by(BrokerAccount.created_at)
        ))
    await ws.accept()
    bus: EventBus = app.state.events
    async with bus.subscribe([order_channel(a) for a in accounts]) as stream:
        await ws.send_json({"type": "subscribed", "accounts": accounts})

        async def pump() -> None:
            async for event in stream:
                await ws.send_json(event)

        # Forward events until the client goes away; receiving is how a disconnect shows up.
        task = asyncio.create_task(pump())
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
