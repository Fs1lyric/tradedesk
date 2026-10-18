"""JSON trading-rule DSL, validated with Pydantic, evaluated against a nested feature dict."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Op = Literal[">", ">=", "<", "<=", "==", "!=", "in", "not_in", "between"]

_CMP: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}


class Condition(BaseModel):
    feature: str = Field(min_length=1)
    op: Op
    value: Any

    @model_validator(mode="after")
    def _check_value(self) -> Condition:
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            raise ValueError(f"'{self.op}' needs a list value")
        if self.op == "between" and not (isinstance(self.value, list) and len(self.value) == 2):
            raise ValueError("'between' needs [low, high]")
        return self


class AllOf(BaseModel):
    all: list[Node] = Field(min_length=1)


class AnyOf(BaseModel):
    any: list[Node] = Field(min_length=1)


Node = Condition | AllOf | AnyOf
AllOf.model_rebuild()
AnyOf.model_rebuild()


class Universe(BaseModel):
    watchlist_id: str | None = None
    symbols: list[str] | None = None


class Action(BaseModel):
    side: Literal["buy", "short", "sell", "cover"]
    sizing: Literal["atr_risk", "fixed_qty", "fixed_notional"] = "atr_risk"
    risk_per_trade: float = Field(0.005, gt=0, le=0.02)
    qty: float | None = Field(None, gt=0)
    notional: float | None = Field(None, gt=0)
    order: Literal["market", "limit", "bracket"] = "bracket"
    stop_atr: float = Field(2.0, gt=0, le=10)
    target_atr: float = Field(3.0, gt=0, le=20)
    time_stop_bars: int | None = Field(None, gt=0)


class Rule(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    universe: Universe = Universe()
    when: Node
    action: Action
    cooldown_minutes: int = Field(240, ge=0)
    mode: Literal["manual", "approve", "auto"] = "approve"


def _lookup(ctx: dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def evaluate(node: Node, ctx: dict[str, Any]) -> bool:
    if isinstance(node, AllOf):
        return all(evaluate(n, ctx) for n in node.all)
    if isinstance(node, AnyOf):
        return any(evaluate(n, ctx) for n in node.any)
    try:
        x = _lookup(ctx, node.feature)
    except KeyError:
        return False
    if x is None:
        return False
    try:
        if node.op == "in":
            return bool(x in node.value)
        if node.op == "not_in":
            return bool(x not in node.value)
        if node.op == "between":
            lo, hi = node.value
            return bool(lo <= x <= hi)
        return bool(_CMP[node.op](x, node.value))
    except TypeError:
        return False
