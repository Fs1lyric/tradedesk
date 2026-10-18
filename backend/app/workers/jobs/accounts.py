"""Per-account housekeeping: reconcile orders, snapshot equity, expire recommendations."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BrokerAccount, EquitySnapshot, Recommendation
from app.runtime import Runtime

log = logging.getLogger(__name__)


async def all_accounts(sessions: async_sessionmaker[AsyncSession]) -> list[BrokerAccount]:
    async with sessions() as s:
        return list(await s.scalars(select(BrokerAccount).order_by(BrokerAccount.created_at)))


async def reconcile_all(rt: Runtime) -> dict[str, list[str]]:
    """Reconcile every account; returns the changed order ids per account."""
    out: dict[str, list[str]] = {}
    for acct in await all_accounts(rt.sessions):
        try:
            async with rt.execution(acct) as svc:
                changed = await svc.reconcile(acct.id)
        except Exception:
            log.exception("reconcile failed for %s", acct.id)
            continue
        if changed:
            out[acct.id] = changed
    return out


async def snapshot_equity(rt: Runtime, now: datetime) -> int:
    """Record each account's equity at `now`; returns how many were written."""
    n = 0
    for acct in await all_accounts(rt.sessions):
        try:
            async with rt.broker(acct) as broker:
                a = await broker.get_account()
        except Exception:
            log.exception("equity snapshot failed for %s", acct.id)
            continue
        values = {"equity": a.equity, "cash": a.cash, "buying_power": a.buying_power}
        async with rt.sessions.begin() as s:
            await s.execute(
                insert(EquitySnapshot).values(account_id=acct.id, ts=now, **values)
                .on_conflict_do_update(index_elements=["account_id", "ts"], set_=values)
            )
        n += 1
    return n


async def expire_recommendations(sessions: async_sessionmaker[AsyncSession], now: datetime
                                 ) -> int:
    async with sessions.begin() as s:
        result = await s.execute(
            update(Recommendation)
            .where(Recommendation.status == "pending", Recommendation.expires_at <= now)
            .values(status="expired")
        )
    return int(result.rowcount)  # type: ignore[attr-defined]
