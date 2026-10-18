"""Trading rules per broker account; the definition is the rules DSL as JSON."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, is_uuid, not_found, owned_account
from app.models import BrokerAccount, Rule, User, Watchlist
from app.services.rules import dsl

router = APIRouter(tags=["rules"])


class RuleOut(BaseModel):
    id: str
    account_id: str
    name: str
    mode: str
    enabled: bool
    definition: dict[str, Any]
    created_at: datetime


class RulePatch(BaseModel):
    enabled: bool | None = None
    definition: dsl.Rule | None = None


def _out(rule: Rule) -> RuleOut:
    return RuleOut(id=rule.id, account_id=rule.account_id, name=rule.name, mode=rule.mode,
                   enabled=rule.enabled, definition=rule.definition, created_at=rule.created_at)


async def _check_universe(session: AsyncSession, user: User, definition: dsl.Rule) -> None:
    wl_id = definition.universe.watchlist_id
    if wl_id is None:
        return
    wl = await session.get(Watchlist, wl_id) if is_uuid(wl_id) else None
    if wl is None or wl.user_id != user.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown watchlist")


def _apply(rule: Rule, definition: dsl.Rule) -> None:
    rule.name = definition.name
    rule.mode = definition.mode
    rule.definition = definition.model_dump(mode="json")


async def _owned(session: AsyncSession, user: User, rule_id: str) -> Rule:
    rule = None
    if is_uuid(rule_id):
        rule = await session.scalar(
            select(Rule).join(BrokerAccount, BrokerAccount.id == Rule.account_id)
            .where(Rule.id == rule_id, BrokerAccount.user_id == user.id)
        )
    if rule is None:
        raise not_found("rule not found")
    return rule


@router.post("/accounts/{account_id}/rules", status_code=status.HTTP_201_CREATED,
             response_model=RuleOut)
async def create_rule(
    account_id: str, body: dsl.Rule, user: UserDep, session: SessionDep
) -> RuleOut:
    acct = await owned_account(session, user, account_id)
    await _check_universe(session, user, body)
    rule = Rule(account_id=acct.id, enabled=True)
    _apply(rule, body)
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return _out(rule)


@router.get("/accounts/{account_id}/rules", response_model=list[RuleOut])
async def list_rules(account_id: str, user: UserDep, session: SessionDep) -> list[RuleOut]:
    acct = await owned_account(session, user, account_id)
    rows = await session.scalars(
        select(Rule).where(Rule.account_id == acct.id).order_by(Rule.created_at)
    )
    return [_out(r) for r in rows]


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def patch_rule(rule_id: str, body: RulePatch, user: UserDep, session: SessionDep
                     ) -> RuleOut:
    rule = await _owned(session, user, rule_id)
    if body.definition is not None:
        await _check_universe(session, user, body.definition)
        _apply(rule, body.definition)
    if body.enabled is not None:
        rule.enabled = body.enabled
    return _out(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: str, user: UserDep, session: SessionDep) -> None:
    await session.delete(await _owned(session, user, rule_id))
