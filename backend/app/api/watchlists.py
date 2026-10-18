"""Named symbol lists per user."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, is_uuid, not_found
from app.models import User, Watchlist, WatchlistSymbol

router = APIRouter(prefix="/watchlists", tags=["watchlists"])

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def normalize_symbol(raw: str) -> str:
    sym = raw.strip().upper()
    if not _SYMBOL.match(sym):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid symbol")
    return sym


SymbolPath = Annotated[str, Path(max_length=16)]


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WatchlistOut(BaseModel):
    id: str
    name: str
    symbols: list[str]


async def _owned(session: AsyncSession, user: User, watchlist_id: str) -> Watchlist:
    wl = await session.get(Watchlist, watchlist_id) if is_uuid(watchlist_id) else None
    if wl is None or wl.user_id != user.id:
        raise not_found("watchlist not found")
    return wl


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WatchlistOut)
async def create_watchlist(body: WatchlistIn, user: UserDep, session: SessionDep
                           ) -> WatchlistOut:
    wl = Watchlist(user_id=user.id, name=body.name)
    session.add(wl)
    await session.flush()
    return WatchlistOut(id=wl.id, name=wl.name, symbols=[])


@router.get("", response_model=list[WatchlistOut])
async def list_watchlists(user: UserDep, session: SessionDep) -> list[WatchlistOut]:
    lists = list(await session.scalars(
        select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.created_at)
    ))
    rows = await session.execute(
        select(WatchlistSymbol.watchlist_id, WatchlistSymbol.symbol)
        .where(WatchlistSymbol.watchlist_id.in_([w.id for w in lists]))
        .order_by(WatchlistSymbol.symbol)
    )
    symbols: dict[str, list[str]] = {w.id: [] for w in lists}
    for wl_id, sym in rows:
        symbols[wl_id].append(sym)
    return [WatchlistOut(id=w.id, name=w.name, symbols=symbols[w.id]) for w in lists]


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(watchlist_id: str, user: UserDep, session: SessionDep) -> None:
    await session.delete(await _owned(session, user, watchlist_id))


@router.put("/{watchlist_id}/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def add_symbol(
    watchlist_id: str, symbol: SymbolPath, user: UserDep, session: SessionDep
) -> None:
    wl = await _owned(session, user, watchlist_id)
    await session.execute(
        insert(WatchlistSymbol).values(watchlist_id=wl.id, symbol=normalize_symbol(symbol))
        .on_conflict_do_nothing()
    )


@router.delete("/{watchlist_id}/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_symbol(
    watchlist_id: str, symbol: SymbolPath, user: UserDep, session: SessionDep
) -> None:
    wl = await _owned(session, user, watchlist_id)
    await session.execute(delete(WatchlistSymbol).where(
        WatchlistSymbol.watchlist_id == wl.id,
        WatchlistSymbol.symbol == normalize_symbol(symbol),
    ))
