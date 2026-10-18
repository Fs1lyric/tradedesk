from datetime import UTC, datetime, timedelta

from app.services.brokers.base import BrokerOrder
from app.services.brokers.polling import diff_orders, us_market_open


def o(status: str, filled: float = 0, avg: float | None = None) -> BrokerOrder:
    return BrokerOrder("1", "c1", "AAPL", "buy", 10, status, filled, avg)


def test_first_sighting_is_a_new_event_and_fills_are_split_out():
    ups, state = diff_orders({}, [o("accepted")])
    assert [u.event for u in ups] == ["new"]
    ups, state = diff_orders(state, [o("partially_filled", 4, 100.0)])
    assert [(u.event, u.fill_qty, u.fill_price) for u in ups] == [("partial_fill", 4, 100.0)]
    ups, state = diff_orders(state, [o("filled", 10, 101.2)])
    (u,) = ups
    assert u.event == "fill" and u.fill_qty == 6
    assert abs(u.fill_price - 102.0) < 1e-9  # (10*101.2 - 4*100) / 6
    assert u.fill_id == "1:10"
    assert diff_orders(state, [o("filled", 10, 101.2)])[0] == []


def test_status_change_without_fill():
    _, state = diff_orders({}, [o("accepted")])
    ups, _ = diff_orders(state, [o("canceled")])
    assert [(u.event, u.fill_qty) for u in ups] == [("canceled", None)]


def test_us_market_hours():
    et_open = datetime(2026, 3, 2, 14, 31, tzinfo=UTC)  # Monday 09:31 EST
    assert us_market_open(et_open)
    assert not us_market_open(et_open - timedelta(minutes=2))
    assert not us_market_open(datetime(2026, 3, 2, 21, 0, tzinfo=UTC))  # 16:00 EST
    assert not us_market_open(datetime(2026, 3, 7, 15, 0, tzinfo=UTC))  # Saturday
    assert us_market_open(datetime(2026, 7, 1, 13, 45, tzinfo=UTC))  # 09:45 EDT
