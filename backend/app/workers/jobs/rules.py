"""Evaluate trading rules against the latest signals and turn hits into recommendations,
executing them straight away for rules in auto mode."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models import (
    BrokerAccount,
    Cooldown,
    Recommendation,
    Rule,
    SignalSnapshot,
    WatchlistSymbol,
)
from app.runtime import Runtime
from app.services.backtest.costs import CostModel
from app.services.execution.service import TradeIntent
from app.services.execution.state import OrderStatus
from app.services.risk.manager import RiskLimits
from app.services.rules import dsl
from app.services.signals.composite import (
    Candidate,
    RankFilters,
    exits,
    position_size,
    rank_candidates,
)
from app.workers.jobs.signals import HORIZON, latest_snapshots

log = logging.getLogger(__name__)

RECOMMENDATION_TTL = timedelta(minutes=30)
ENTRY_SIDES = {"buy", "short"}


def rule_context(snap: SignalSnapshot) -> dict[str, Any]:
    """What a rule condition can reference: bare feature names, `signal.*` and `news.*`."""
    f = snap.features
    signal = {**f, "direction_score": snap.direction_score,
              "expected_return": snap.expected_return, "support": snap.support}
    news = {"sentiment": f.get("sentiment"), "attention_z": f.get("attention_z"),
            "count_24h": f.get("news_count_24h"),
            "impact": f.get("sentiment")}  # sentiment is tanh of the decayed impact sum
    return {**f, "signal": signal, "news": news, "price": f.get("close"),
            "symbol": snap.symbol}


def _candidate(snap: SignalSnapshot, costs: CostModel) -> Candidate:
    f = snap.features
    sigma_h = (f.get("sigma_daily") or 0.0) * math.sqrt(HORIZON)
    return Candidate(
        symbol=snap.symbol, expected_return=snap.expected_return, sigma_h=sigma_h,
        cost=2 * (costs.slippage_bps + costs.spread_bps / 2) / 1e4, support=snap.support,
        price=f.get("close") or 0.0, adv_dollars=f.get("adv_dollars") or 0.0,
        regime_long=f.get("regime_long") or 0.0, sentiment=f.get("sentiment") or 0.0,
        earnings_within_days=None,
    )


def _qty(action: dsl.Action, equity: float, price: float, atr: float, limits: RiskLimits
         ) -> float:
    if action.sizing == "fixed_qty":
        return float(action.qty or 0)
    if action.sizing == "fixed_notional":
        return float(math.floor((action.notional or 0) / price)) if price > 0 else 0.0
    return float(position_size(equity, price, atr, action.risk_per_trade, action.stop_atr,
                               limits.max_position_pct))


async def _universe(rt: Runtime, d: dsl.Rule) -> list[str]:
    if d.universe.symbols:
        return d.universe.symbols
    if d.universe.watchlist_id:
        async with rt.sessions() as s:
            return list(await s.scalars(select(WatchlistSymbol.symbol).where(
                WatchlistSymbol.watchlist_id == d.universe.watchlist_id)))
    return []


async def run_rules(rt: Runtime, now: datetime, costs: CostModel | None = None) -> list[str]:
    """Returns the ids of recommendations created."""
    costs = costs or CostModel()
    async with rt.sessions() as s:
        pairs = (await s.execute(
            select(Rule, BrokerAccount).join(BrokerAccount, BrokerAccount.id == Rule.account_id)
            .where(Rule.enabled, BrokerAccount.kill_switch.is_(False))
            .order_by(Rule.created_at)
        )).all()
    made: list[str] = []
    for acct in {a.id: a for _, a in pairs}.values():
        rules = [r for r, a in pairs if a.id == acct.id]
        try:
            made += await _run_account(rt, acct, rules, now, costs)
        except Exception:
            log.exception("rules failed for account %s", acct.id)
    return made


async def _run_account(rt: Runtime, acct: BrokerAccount, rules: list[Rule], now: datetime,
                       costs: CostModel) -> list[str]:
    limits = RiskLimits.from_dict(acct.risk_limits)
    async with rt.broker(acct) as broker:
        equity = (await broker.get_account()).equity
        held = {p.symbol: p.qty for p in await broker.get_positions()}
    async with rt.sessions() as s:
        pending = set((await s.execute(
            select(Recommendation.rule_id, Recommendation.symbol)
            .where(Recommendation.account_id == acct.id, Recommendation.status == "pending")
        )).tuples())
        cooling = set((await s.execute(
            select(Cooldown.rule_id, Cooldown.symbol)
            .where(Cooldown.account_id == acct.id, Cooldown.until > now)
        )).tuples())

    made = []
    for rule in rules:
        d = dsl.Rule.model_validate(rule.definition)
        symbols = await _universe(rt, d)
        async with rt.sessions() as s:
            snaps = await latest_snapshots(s, symbols, now)
        for sym, snap in sorted(snaps.items()):
            if (rule.id, sym) in pending or (rule.id, sym) in cooling:
                continue
            if not dsl.evaluate(d.when, rule_context(snap)):
                continue
            rec = _recommend(rule, d, snap, equity, held.get(sym, 0.0), limits, costs, now)
            if rec is None:
                continue
            rec.account_id = acct.id
            async with rt.sessions.begin() as s:
                s.add(rec)
            made.append(rec.id)
            if rule.mode == "auto":
                await _execute(rt, acct, rec, d)
    return made


def _recommend(rule: Rule, d: dsl.Rule, snap: SignalSnapshot, equity: float, held: float,
               limits: RiskLimits, costs: CostModel, now: datetime) -> Recommendation | None:
    side, f = d.action.side, snap.features
    price, atr = f.get("close") or 0.0, f.get("atr") or 0.0
    score, confidence = 0.0, 0.0
    if side in ENTRY_SIDES:
        ranked = rank_candidates([_candidate(snap, costs)],
                                 RankFilters(allow_short=side == "short"))
        if not ranked or ranked[0].side != side:
            return None
        score, confidence = ranked[0].score, ranked[0].confidence
        qty = _qty(d.action, equity, price, atr, limits)
    else:  # exits close what's held
        if (side == "sell" and held <= 0) or (side == "cover" and held >= 0):
            return None
        qty = abs(held)
    if qty <= 0:
        return None
    stop = target = None
    if side in ENTRY_SIDES and d.action.order == "bracket" and atr > 0:
        stop, target = exits(price, atr, side, d.action.stop_atr, d.action.target_atr)
    return Recommendation(
        id=str(uuid.uuid4()), rule_id=rule.id, symbol=snap.symbol, side=side, qty=qty,
        entry_price=price, stop_loss=stop, take_profit=target,
        expected_return=snap.expected_return, score=score, confidence=confidence,
        rationale={"rule": d.name, "direction_score": snap.direction_score,
                   "signal_ts": snap.ts.isoformat(),
                   "features": {k: f.get(k) for k in
                                ("trend", "momentum", "rsi", "breakout", "volume",
                                 "sentiment")}},
        status="pending", expires_at=now + RECOMMENDATION_TTL,
    )


async def _execute(rt: Runtime, acct: BrokerAccount, rec: Recommendation, d: dsl.Rule) -> None:
    intent = TradeIntent(
        key=f"rec:{rec.id}", account_id=acct.id, symbol=rec.symbol, side=rec.side,
        qty=rec.qty, order_type="market" if d.action.order == "market" else "limit",
        stop_loss=rec.stop_loss, take_profit=rec.take_profit, origin="rule",
        rule_id=rec.rule_id, recommendation_id=rec.id, cooldown_minutes=d.cooldown_minutes,
    )
    async with rt.execution(acct) as svc:
        result = await svc.execute(intent)
    placed = result.order is not None and result.order.status != OrderStatus.BROKER_REJECTED
    async with rt.sessions.begin() as s:
        row = await s.get_one(Recommendation, rec.id)
        row.status = "executed" if placed else "rejected"
