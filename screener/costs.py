"""NEPSE transaction costs and capital-gains tax."""
from __future__ import annotations


class Costs:
    def __init__(self, cfg: dict):
        c = cfg.get("costs", {})
        self.tiers = [(t[0] if t[0] is not None else float("inf"), float(t[1])) for t in c.get("broker_commission_tiers", [[None, 0.004]])]
        self.min_commission = c.get("min_commission", 10)
        self.sebon = c.get("sebon_fee", 0.00015)
        self.dp = c.get("dp_charge", 25)
        self.cgt_long = c.get("cgt_long_term", 0.0375)
        self.cgt_short = c.get("cgt_short_term", 0.05)
        self.long_days = c.get("long_term_days", 365)

    def commission(self, value: float) -> float:
        if value <= 0:
            return 0.0
        for limit, rate in self.tiers:
            if value <= limit:
                return max(self.min_commission, value * rate)
        return max(self.min_commission, value * self.tiers[-1][1])

    def buy_cost(self, value: float) -> float:
        return self.commission(value) + value * self.sebon

    def sell_cost(self, value: float) -> float:
        return self.commission(value) + value * self.sebon + self.dp

    def cgt_rate(self, hold_calendar_days: int) -> float:
        return self.cgt_long if hold_calendar_days > self.long_days else self.cgt_short

    def cgt(self, gain: float, hold_calendar_days: int) -> float:
        return max(0.0, gain) * self.cgt_rate(hold_calendar_days)

    def round_trip_pct(self, value: float) -> float:
        return (self.buy_cost(value) + self.sell_cost(value)) / value if value else 0.0

    def sell_breakdown(self, qty: float, price: float, cost_basis_total: float, hold_days: int) -> dict:
        value = qty * price
        fees = self.sell_cost(value)
        gain = value - fees - cost_basis_total
        tax = self.cgt(gain, hold_days)
        return {"gross": value, "fees": fees, "gain_before_tax": gain, "cgt": tax,
                "cgt_rate": self.cgt_rate(hold_days), "net_proceeds": value - fees - tax,
                "net_profit": gain - tax}
