from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.services.risk.manager import AccountState, OrderIntent, Quote, RiskLimits, RiskManager

NOW = datetime(2026, 3, 2, 15, tzinfo=UTC)  # Monday 10:00 ET


def state(**kw):
    base = AccountState(
        equity=100_000, buying_power=100_000, start_of_day_equity=100_000,
        positions={}, market_open=True, kill_switch=False, halted=False,
        orders_last_minute=0, orders_today=0, day_trades_5d=0,
        recent_intent_keys=set(), cooldowns={}, tradable={"AAPL"}, shortable={"AAPL"},
    )
    return replace(base, **kw)


def intent(**kw):
    base = OrderIntent(
        key="i1", symbol="AAPL", side="buy", qty=50, limit_price=100.2,
        quote=Quote(bid=99.9, ask=100.1, ts=NOW - timedelta(seconds=1)),
        rule_id=None, would_be_day_trade=False, extended_hours=False,
    )
    return replace(base, **kw)


def failed(result):
    return {c.name for c in result.checks if not c.passed}


rm = RiskManager(RiskLimits())


def test_clean_order_passes_every_check():
    r = rm.check(intent(), state(), NOW)
    assert r.passed, failed(r)
    assert len(r.checks) >= 9


def test_kill_switch_and_halt():
    assert "kill_switch" in failed(rm.check(intent(), state(kill_switch=True), NOW))
    assert "kill_switch" in failed(rm.check(intent(), state(halted=True), NOW))


def test_market_closed_unless_extended_limit():
    assert "market_hours" in failed(rm.check(intent(), state(market_open=False), NOW))
    r = rm.check(intent(extended_hours=True), state(market_open=False), NOW)
    assert "market_hours" not in failed(r)


def test_daily_loss_limit():
    r = rm.check(intent(), state(equity=97_900), NOW)
    assert "daily_loss" in failed(r)
    assert r.halt_account


def test_position_and_exposure_limits():
    assert "max_position" in failed(rm.check(intent(qty=200), state(), NOW))  # 20k > 10%
    held = {"MSFT": 95_000.0}
    assert "gross_exposure" in failed(rm.check(intent(), state(positions=held), NOW))


def test_reducing_order_is_not_blocked_by_position_limit():
    r = rm.check(intent(side="sell", qty=200), state(positions={"AAPL": 30_000.0}), NOW)
    assert "max_position" not in failed(r)
    assert "gross_exposure" not in failed(r)


def test_order_rate():
    assert "order_rate" in failed(rm.check(intent(), state(orders_last_minute=5), NOW))


def test_pdt_only_under_25k():
    small = state(equity=20_000, start_of_day_equity=20_000, buying_power=20_000,
                  day_trades_5d=3)
    r = rm.check(intent(would_be_day_trade=True, qty=10), small, NOW)
    assert "pdt" in failed(r)
    big = state(day_trades_5d=3)
    assert "pdt" not in failed(rm.check(intent(would_be_day_trade=True), big, NOW))


def test_buying_power_and_tradability():
    assert "buying_power" in failed(rm.check(intent(), state(buying_power=1_000), NOW))
    assert "tradable" in failed(rm.check(intent(symbol="ZZZ"), state(), NOW))
    r = rm.check(intent(side="short"), state(shortable=set()), NOW)
    assert "tradable" in failed(r)


def test_price_sanity_and_stale_quote():
    assert "price_sanity" in failed(rm.check(intent(limit_price=105), state(), NOW))
    stale = Quote(bid=99.9, ask=100.1, ts=NOW - timedelta(seconds=30))
    assert "price_sanity" in failed(rm.check(intent(quote=stale), state(), NOW))


def test_duplicate_and_cooldown():
    assert "duplicate" in failed(rm.check(intent(), state(recent_intent_keys={"i1"}), NOW))
    cd = {("r1", "AAPL"): NOW + timedelta(minutes=10)}
    assert "duplicate" in failed(rm.check(intent(rule_id="r1"), state(cooldowns=cd), NOW))


def test_check_results_are_plain_bools_even_with_numpy_quotes():
    import json

    import numpy as np

    o = intent(quote=Quote(np.float64(99.95), np.float64(100.05), NOW))
    result = RiskManager(RiskLimits()).check(o, state(), NOW)
    json.dumps(result.to_dict())  # raises on numpy bools
