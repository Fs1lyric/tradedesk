"""Backtest runs. The API only queues a row; the worker runs it and fills in the result."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, is_uuid, not_found
from app.api.watchlists import normalize_symbol
from app.models import BacktestRun, User
from app.services.backtest.strategies import STRATEGIES, build_strategy

router = APIRouter(prefix="/backtests", tags=["backtests"])


class BacktestIn(BaseModel):
    kind: Literal["backtest", "walkforward", "event_study", "compare"]
    strategy: str
    symbols: list[str] = Field(min_length=1, max_length=100)
    start: date
    end: date
    strategy_params: dict[str, Any] = {}
    options: dict[str, Any] = {}  # kind-specific: folds, trials, event filters, benchmark

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_symbol(s) for s in v))

    @model_validator(mode="after")
    def _check(self) -> BacktestIn:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy; one of {sorted(STRATEGIES)}")
        try:
            build_strategy(self.strategy, self.strategy_params)
        except (TypeError, ValueError) as e:
            raise ValueError(f"bad strategy_params: {e}") from e
        return self


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    strategy: str
    params: dict[str, Any]
    status: str
    metrics: dict[str, Any] | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class BacktestDetail(BacktestOut):
    result: dict[str, Any] | None


async def _owned(session: AsyncSession, user: User, run_id: str) -> BacktestRun:
    run = await session.get(BacktestRun, run_id) if is_uuid(run_id) else None
    if run is None or run.user_id != user.id:
        raise not_found("backtest not found")
    return run


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=BacktestOut)
async def queue_backtest(body: BacktestIn, user: UserDep, session: SessionDep) -> BacktestRun:
    run = BacktestRun(
        user_id=user.id, kind=body.kind, strategy=body.strategy, status="queued",
        params=body.model_dump(mode="json", exclude={"kind", "strategy"}),
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


@router.get("", response_model=list[BacktestOut])
async def list_backtests(user: UserDep, session: SessionDep) -> list[BacktestRun]:
    rows = await session.scalars(
        select(BacktestRun).where(BacktestRun.user_id == user.id)
        .order_by(BacktestRun.created_at.desc()).limit(200)
    )
    return list(rows)


@router.get("/{run_id}", response_model=BacktestDetail)
async def get_backtest(run_id: str, user: UserDep, session: SessionDep) -> BacktestRun:
    return await _owned(session, user, run_id)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backtest(run_id: str, user: UserDep, session: SessionDep) -> None:
    await session.delete(await _owned(session, user, run_id))
