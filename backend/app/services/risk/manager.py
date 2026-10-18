"""Pre-trade risk checks. Every order, live or backtest, goes through RiskManager.check."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

PDT_EQUITY = 25_000.0
SIDE_SIGN = {"buy": 1, "cover": 1, "sell": -1, "short": -1}


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss_pct: float = 0.02
    max_position_pct: float = 0.10
    max_gross_exposure: float = 1.0
    max_orders_per_minute: int = 5
    max_orders_per_day: int = 200
    max_quote_age_s: float = 5.0
    max_limit_deviation: float = 0.01
    risk_per_trade: float = 0.005

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RiskLimits:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class OrderIntent:
    key: str
    symbol: str
    side: str  # buy | sell | short | cover
    qty: float
    limit_price: float | None
    quote: Quote
    rule_id: str | None = None
    would_be_day_trade: bool = False
    extended_hours: bool = False

    @property
    def ref_price(self) -> float:
        if self.limit_price is not None:
            return self.limit_price
        return self.quote.ask if SIDE_SIGN[self.side] > 0 else self.quote.bid


@dataclass(frozen=True)
class AccountState:
    equity: float
    buying_power: float
    start_of_day_equity: float
    positions: dict[str, float]  # symbol -> signed market value
    market_open: bool
    kill_switch: bool
    halted: bool
    orders_last_minute: int
    orders_today: int
    day_trades_5d: int
    recent_intent_keys: set[str] = field(default_factory=set)
    cooldowns: dict[tuple[str, str], datetime] = field(default_factory=dict)
    tradable: set[str] = field(default_factory=set)
    shortable: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RiskResult:
    checks: list[Check]
    halt_account: bool = False

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "halt_account": self.halt_account,
            "checks": [asdict(c) for c in self.checks],
        }


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def check(self, o: OrderIntent, s: AccountState, now: datetime) -> RiskResult:
        lim = self.limits
        checks: list[Check] = []
        halt = False

        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append(Check(name, ok, detail))

        price = o.ref_price
        delta = SIDE_SIGN[o.side] * o.qty * price
        current = s.positions.get(o.symbol, 0.0)
        after = current + delta
        reducing = abs(after) < abs(current) and (after == 0 or (after > 0) == (current > 0))

        add("kill_switch", not (s.kill_switch or s.halted),
            "kill switch on" if s.kill_switch else ("account halted" if s.halted else ""))
        add("market_hours", s.market_open or (o.extended_hours and o.limit_price is not None))

        day_ret = s.equity / s.start_of_day_equity - 1 if s.start_of_day_equity else 0.0
        loss_ok = day_ret > -lim.max_daily_loss_pct
        halt = not loss_ok
        add("daily_loss", loss_ok, f"day return {day_ret:.2%}")

        pos_pct = abs(after) / s.equity if s.equity > 0 else float("inf")
        add("max_position", reducing or pos_pct <= lim.max_position_pct, f"{pos_pct:.1%}")

        others = sum(abs(v) for k, v in s.positions.items() if k != o.symbol)
        gross = (others + abs(after)) / s.equity if s.equity > 0 else float("inf")
        add("gross_exposure", reducing or gross <= lim.max_gross_exposure, f"{gross:.2f}x")

        add("order_rate",
            s.orders_last_minute < lim.max_orders_per_minute
            and s.orders_today < lim.max_orders_per_day,
            f"{s.orders_last_minute}/min, {s.orders_today}/day")

        pdt_block = s.equity < PDT_EQUITY and o.would_be_day_trade and s.day_trades_5d >= 3
        add("pdt", not pdt_block, f"{s.day_trades_5d} day trades in 5 days")

        added_exposure = max(0.0, abs(after) - abs(current))
        add("buying_power", added_exposure <= s.buying_power, f"needs {added_exposure:,.0f}")

        tradable = o.symbol in s.tradable and (o.side != "short" or o.symbol in s.shortable)
        add("tradable", tradable)

        age = (now - o.quote.ts).total_seconds()
        dev = abs(price / o.quote.mid - 1) if o.quote.mid > 0 else float("inf")
        add("price_sanity",
            age <= lim.max_quote_age_s and dev <= lim.max_limit_deviation,
            f"quote age {age:.1f}s, deviation {dev:.2%}")

        cooling = o.rule_id is not None and s.cooldowns.get((o.rule_id, o.symbol), now) > now
        add("duplicate", o.key not in s.recent_intent_keys and not cooling,
            "cooldown active" if cooling else "")
        return RiskResult(checks, halt_account=halt)


def cooldown_until(now: datetime, minutes: int) -> datetime:
    return now + timedelta(minutes=minutes)
