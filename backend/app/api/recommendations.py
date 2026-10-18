"""Recommendations from the signal pipeline. Approving one sends it through the same
risk-gated ExecutionService that rules use."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, is_uuid, not_found, owned_account
from app.api.trading import OrderOut, execution_service
from app.models import AuditLog, BrokerAccount, Recommendation, User
from app.services.execution.service import TradeIntent
from app.services.execution.state import OrderStatus

router = APIRouter(tags=["recommendations"])


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    rule_id: str | None
    symbol: str
    side: str
    qty: float
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    expected_return: float
    score: float
    confidence: float
    rationale: dict[str, Any]
    status: str
    created_at: datetime
    expires_at: datetime | None


class Approval(BaseModel):
    recommendation: RecommendationOut
    risk: dict[str, Any]
    order: OrderOut | None


async def _owned_pending(session: AsyncSession, user: User, rec_id: str) -> Recommendation:
    """Lock the user's recommendation so two approvals can't both execute it."""
    rec = None
    if is_uuid(rec_id):
        rec = await session.scalar(
            select(Recommendation)
            .join(BrokerAccount, BrokerAccount.id == Recommendation.account_id)
            .where(Recommendation.id == rec_id, BrokerAccount.user_id == user.id)
            .with_for_update(of=Recommendation)
        )
    if rec is None:
        raise not_found("recommendation not found")
    if rec.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"recommendation is {rec.status}")
    return rec


@router.get("/accounts/{account_id}/recommendations", response_model=list[RecommendationOut])
async def list_recommendations(
    account_id: str, user: UserDep, session: SessionDep,
    status: Literal["pending", "all"] = "pending", limit: int = 100,
) -> list[Recommendation]:
    acct = await owned_account(session, user, account_id)
    q = select(Recommendation).where(Recommendation.account_id == acct.id)
    if status == "pending":
        q = q.where(Recommendation.status == "pending")
    q = q.order_by(Recommendation.created_at.desc()).limit(min(max(limit, 1), 500))
    return list(await session.scalars(q))


@router.post("/recommendations/{rec_id}/approve", response_model=Approval)
async def approve(
    rec_id: str, request: Request, user: UserDep, session: SessionDep
) -> Approval | JSONResponse:
    rec = await _owned_pending(session, user, rec_id)
    if rec.expires_at is not None and rec.expires_at <= datetime.now(UTC):
        rec.status = "expired"
        # Returned, not raised, so the status change commits.
        return JSONResponse({"detail": "recommendation is expired"},
                            status.HTTP_409_CONFLICT)
    acct = await session.get_one(BrokerAccount, rec.account_id)
    intent = TradeIntent(
        key=f"rec:{rec.id}", account_id=acct.id, symbol=rec.symbol, side=rec.side,
        qty=rec.qty, stop_loss=rec.stop_loss, take_profit=rec.take_profit,
        origin="recommendation", rule_id=rec.rule_id, recommendation_id=rec.id,
    )
    async with execution_service(request, acct) as svc:
        result = await svc.execute(intent)
    placed = result.order is not None and result.order.status != OrderStatus.BROKER_REJECTED
    rec.status = "executed" if placed else "rejected"
    session.add(AuditLog(user_id=user.id, action="recommendation_approved",
                         detail={"recommendation_id": rec.id, "status": rec.status}))
    return Approval(
        recommendation=RecommendationOut.model_validate(rec), risk=result.risk.to_dict(),
        order=OrderOut.model_validate(result.order) if result.order else None,
    )


@router.post("/recommendations/{rec_id}/dismiss", response_model=RecommendationOut)
async def dismiss(rec_id: str, user: UserDep, session: SessionDep) -> Recommendation:
    rec = await _owned_pending(session, user, rec_id)
    rec.status = "dismissed"
    return rec
