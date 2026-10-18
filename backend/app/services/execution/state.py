"""Order lifecycle state machine."""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    NEW = "NEW"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTED = "SUBMITTED"
    BROKER_REJECTED = "BROKER_REJECTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL


S = OrderStatus
TERMINAL = {S.RISK_REJECTED, S.BROKER_REJECTED, S.FILLED, S.CANCELED, S.EXPIRED}
_LIVE_EXITS = {S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED}

ALLOWED: dict[OrderStatus, set[OrderStatus]] = {
    S.NEW: {S.SUBMITTED, S.RISK_REJECTED},
    S.SUBMITTED: {S.ACCEPTED, S.BROKER_REJECTED} | _LIVE_EXITS,
    S.ACCEPTED: _LIVE_EXITS,
    S.PARTIALLY_FILLED: _LIVE_EXITS,
}


class InvalidTransition(ValueError):
    pass


def transition(current: OrderStatus, nxt: OrderStatus) -> OrderStatus:
    if nxt == current:
        return current
    if nxt not in ALLOWED.get(current, set()):
        raise InvalidTransition(f"{current} -> {nxt}")
    return nxt


_BROKER_MAP = {
    "pending_new": S.SUBMITTED,
    "accepted": S.ACCEPTED,
    "accepted_for_bidding": S.ACCEPTED,
    "new": S.ACCEPTED,
    "held": S.ACCEPTED,
    "pending_replace": S.ACCEPTED,
    "replaced": S.ACCEPTED,
    "pending_cancel": S.ACCEPTED,
    "calculated": S.ACCEPTED,
    "stopped": S.ACCEPTED,
    "suspended": S.ACCEPTED,
    "partially_filled": S.PARTIALLY_FILLED,
    "partial_fill": S.PARTIALLY_FILLED,
    "filled": S.FILLED,
    "fill": S.FILLED,
    "canceled": S.CANCELED,
    "expired": S.EXPIRED,
    "done_for_day": S.EXPIRED,
    "rejected": S.BROKER_REJECTED,
}


def normalize_broker_status(status: str) -> OrderStatus:
    return _BROKER_MAP.get(status.lower(), S.ACCEPTED)
