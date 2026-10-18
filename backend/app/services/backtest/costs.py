"""Transaction cost model: half spread, slippage, square-root impact and commission."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0
    spread_bps: float = 2.0
    impact_k: float = 0.1
    commission_per_share: float = 0.0
    min_commission: float = 0.0

    def fill_price(
        self, sign: int, ref: float, qty: float, sigma_daily: float = 0.0, adv_shares: float = 0.0
    ) -> float:
        """Price paid (sign=+1, buying) or received (sign=-1, selling); always adverse."""
        impact = 0.0
        if adv_shares > 0:
            impact = self.impact_k * sigma_daily * math.sqrt(qty / adv_shares)
        cost = self.spread_bps / 2 / 1e4 + self.slippage_bps / 1e4 + impact
        return ref * (1 + sign * cost)

    def commission(self, qty: float) -> float:
        if qty <= 0:
            return 0.0
        return max(self.min_commission, self.commission_per_share * qty)

    def round_trip_cost(self, sigma_daily: float = 0.0, participation: float = 0.0) -> float:
        """Approximate round-trip cost as a fraction of notional, for ranking."""
        one_way = (self.spread_bps / 2 + self.slippage_bps) / 1e4
        return 2 * (one_way + self.impact_k * sigma_daily * math.sqrt(participation))
