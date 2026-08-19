"""Vanilla fixed-for-floating interest rate swap pricing off a discount curve.

Single-curve pricing: the floating leg is valued by the classic telescoping
identity PV_float = N * (df(t_start) - df(t_end)), which holds when the
projection and discount curves coincide and there is no spread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from quantdesk.rates.curve import DiscountCurve

SwapSide = Literal["payer", "receiver"]


@dataclass(frozen=True)
class VanillaSwap:
    """Spot-starting vanilla interest rate swap.

    Parameters
    ----------
    notional : notional in currency units
    fixed_rate : the contractual fixed rate
    maturity : in years; must be a whole multiple of the fixed period
    side : "payer" pays fixed / receives float; "receiver" the reverse
    fixed_freq : fixed leg payments per year
    """

    notional: float
    fixed_rate: float
    maturity: float
    side: SwapSide = "payer"
    fixed_freq: int = 1

    def __post_init__(self):
        tau = 1.0 / self.fixed_freq
        n = round(self.maturity * self.fixed_freq)
        if n < 1 or abs(n * tau - self.maturity) > 1e-9:
            raise ValueError("maturity must be a positive multiple of the fixed period")
        if self.side not in ("payer", "receiver"):
            raise ValueError(f"side must be 'payer' or 'receiver', got {self.side!r}")

    def _payment_times(self) -> np.ndarray:
        n = round(self.maturity * self.fixed_freq)
        return np.arange(1, n + 1) / self.fixed_freq

    def annuity(self, curve: DiscountCurve) -> float:
        """PV of 1 unit of fixed rate: sum of tau_i * df(t_i) (the 'PV01')."""
        times = self._payment_times()
        tau = 1.0 / self.fixed_freq
        return float(np.sum(tau * curve.df(times)))

    def par_rate(self, curve: DiscountCurve) -> float:
        """Fixed rate that makes the swap worth zero today."""
        return float((1.0 - curve.df(self.maturity)) / self.annuity(curve))

    def npv(self, curve: DiscountCurve) -> float:
        """Present value from the payer's or receiver's perspective."""
        pv_float = self.notional * (1.0 - curve.df(self.maturity))
        pv_fixed = self.notional * self.fixed_rate * self.annuity(curve)
        value = pv_float - pv_fixed
        return value if self.side == "payer" else -value

    def dv01(self, curve: DiscountCurve, bump: float = 1e-4) -> float:
        """Change in NPV for a +1bp parallel shift of the zero curve.

        Central difference on the bumped curve; sign follows the position
        (a payer swap gains when rates rise, so its DV01 is positive).
        """
        up = self.npv(curve.bumped(+bump))
        down = self.npv(curve.bumped(-bump))
        return (up - down) / 2.0
