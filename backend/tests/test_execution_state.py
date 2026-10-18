import pytest

from app.services.execution.state import (
    InvalidTransition,
    OrderStatus,
    normalize_broker_status,
    transition,
)

S = OrderStatus


def test_happy_path():
    s = S.NEW
    for nxt in [S.SUBMITTED, S.ACCEPTED, S.PARTIALLY_FILLED, S.PARTIALLY_FILLED, S.FILLED]:
        s = transition(s, nxt)
    assert s is S.FILLED and s.terminal


def test_rejections_and_cancels():
    assert transition(S.NEW, S.RISK_REJECTED) is S.RISK_REJECTED
    assert transition(S.SUBMITTED, S.BROKER_REJECTED) is S.BROKER_REJECTED
    assert transition(S.ACCEPTED, S.CANCELED) is S.CANCELED
    assert transition(S.PARTIALLY_FILLED, S.EXPIRED) is S.EXPIRED


def test_fill_can_skip_accepted():
    # a marketable order may report fill before accepted reaches us
    assert transition(S.SUBMITTED, S.FILLED) is S.FILLED


def test_terminal_states_are_final_but_repeats_are_idempotent():
    assert transition(S.FILLED, S.FILLED) is S.FILLED
    with pytest.raises(InvalidTransition):
        transition(S.FILLED, S.CANCELED)
    with pytest.raises(InvalidTransition):
        transition(S.NEW, S.FILLED)


def test_normalize_alpaca_statuses():
    assert normalize_broker_status("new") is S.ACCEPTED
    assert normalize_broker_status("partially_filled") is S.PARTIALLY_FILLED
    assert normalize_broker_status("filled") is S.FILLED
    assert normalize_broker_status("rejected") is S.BROKER_REJECTED
    assert normalize_broker_status("pending_new") is S.SUBMITTED
    assert normalize_broker_status("done_for_day") is S.EXPIRED
