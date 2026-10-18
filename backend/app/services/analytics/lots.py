"""FIFO lot matching: fills -> round-trip trades. Shared by live analytics and the backtester."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

EPS = 1e-9


@dataclass(frozen=True)
class Fill:
    fill_id: str
    symbol: str
    qty: float  # signed: + buy, - sell
    price: float
    filled_at: datetime
    fee: float = 0.0


@dataclass
class Trade:
    symbol: str
    direction: str  # "long" | "short"
    entry_at: datetime
    qty: float = 0.0  # total quantity opened
    entry_notional: float = 0.0
    exit_qty: float = 0.0
    exit_notional: float = 0.0
    fees: float = 0.0
    gross_pnl: float = 0.0
    exit_at: datetime | None = None
    closed: bool = False
    entry_fill_ids: list[str] = field(default_factory=list)
    exit_fill_ids: list[str] = field(default_factory=list)

    @property
    def entry_price(self) -> float:
        return self.entry_notional / self.qty if self.qty else 0.0

    @property
    def exit_price(self) -> float | None:
        return self.exit_notional / self.exit_qty if self.exit_qty else None

    @property
    def realized_pnl(self) -> float:
        return self.gross_pnl - self.fees

    @property
    def pnl(self) -> float:
        return self.realized_pnl

    @property
    def return_pct(self) -> float:
        return self.realized_pnl / self.entry_notional if self.entry_notional else 0.0


def match_fills(fills: list[Fill]) -> list[Trade]:
    by_symbol: dict[str, list[Fill]] = defaultdict(list)
    for fl in fills:
        by_symbol[fl.symbol].append(fl)
    trades: list[Trade] = []
    for sym_fills in by_symbol.values():
        trades.extend(_match_symbol(sorted(sym_fills, key=lambda x: x.filled_at)))
    return sorted(trades, key=lambda t: t.entry_at)


def _match_symbol(fills: list[Fill]) -> list[Trade]:
    trades: list[Trade] = []
    lots: deque[list[float]] = deque()  # [signed qty, price]
    position = 0.0
    current: Trade | None = None

    for fl in fills:
        remaining = fl.qty
        fee_per_unit = fl.fee / abs(fl.qty) if fl.qty else 0.0
        while abs(remaining) > EPS:
            if current is None or position == 0 or (position > 0) == (remaining > 0):
                if current is None or current.closed:
                    current = Trade(fl.symbol, "long" if remaining > 0 else "short", fl.filled_at)
                    trades.append(current)
                lots.append([remaining, fl.price])
                position += remaining
                current.qty += abs(remaining)
                current.entry_notional += abs(remaining) * fl.price
                current.fees += abs(remaining) * fee_per_unit
                current.entry_fill_ids.append(fl.fill_id)
                remaining = 0.0
                continue
            # reducing: match against the oldest lot
            assert current is not None
            lot = lots[0]
            matched = min(abs(remaining), abs(lot[0]))
            sign = 1.0 if lot[0] > 0 else -1.0
            current.gross_pnl += sign * matched * (fl.price - lot[1])
            current.exit_qty += matched
            current.exit_notional += matched * fl.price
            current.fees += matched * fee_per_unit
            if fl.fill_id not in current.exit_fill_ids:
                current.exit_fill_ids.append(fl.fill_id)
            lot[0] -= sign * matched
            position -= sign * matched
            remaining += sign * matched
            if abs(lot[0]) <= EPS:
                lots.popleft()
            if abs(position) <= EPS:
                position = 0.0
                current.closed = True
                current.exit_at = fl.filled_at
    return trades
