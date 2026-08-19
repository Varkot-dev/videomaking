"""Discount curve construction: interpolation and bootstrapping.

Single-curve framework (one curve both discounts and projects forwards),
the standard first model before multi-curve OIS/LIBOR separation.

Interpolation is linear in log discount factors, i.e. piecewise-constant
instantaneous forward rates -- the usual desk default because it can never
produce negative forward discount ratios between nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class DiscountCurve:
    """A discount curve defined by pillar times (years) and discount factors."""

    times: np.ndarray
    dfs: np.ndarray
    _log_dfs: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        times = np.asarray(self.times, dtype=float)
        dfs = np.asarray(self.dfs, dtype=float)
        if times.ndim != 1 or times.shape != dfs.shape:
            raise ValueError("times and dfs must be 1-D arrays of equal length")
        if np.any(times <= 0) or np.any(np.diff(times) <= 0):
            raise ValueError("times must be positive and strictly increasing")
        if np.any(dfs <= 0):
            raise ValueError("discount factors must be positive")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "dfs", dfs)
        object.__setattr__(self, "_log_dfs", np.log(dfs))

    def df(self, t):
        """Discount factor at time t (years). Vectorized.

        Log-linear between pillars; flat-forward extrapolation beyond the
        last pillar; exact df(0) = 1.
        """
        t = np.asarray(t, dtype=float)
        if np.any(t < 0):
            raise ValueError("cannot discount to negative time")
        # Prepend the origin (t=0, log df=0) so early times interpolate correctly.
        xs = np.concatenate([[0.0], self.times])
        ys = np.concatenate([[0.0], self._log_dfs])
        # np.interp clamps beyond the last node; extend with the last forward rate.
        log_df = np.interp(t, xs, ys)
        beyond = t > xs[-1]
        if np.any(beyond):
            fwd = -(ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
            log_df = np.where(beyond, ys[-1] - fwd * (t - xs[-1]), log_df)
        out = np.exp(log_df)
        return float(out) if out.ndim == 0 else out

    def zero_rate(self, t):
        """Continuously compounded zero rate at time t."""
        t = np.asarray(t, dtype=float)
        if np.any(t <= 0):
            raise ValueError("zero rate needs t > 0")
        out = -np.log(self.df(t)) / t
        return float(out) if out.ndim == 0 else out

    def forward_rate(self, t1, t2):
        """Simply compounded forward rate between t1 and t2."""
        t1, t2 = np.asarray(t1, dtype=float), np.asarray(t2, dtype=float)
        if np.any(t2 <= t1):
            raise ValueError("need t2 > t1")
        out = (self.df(t1) / self.df(t2) - 1.0) / (t2 - t1)
        return float(out) if out.ndim == 0 else out

    def bumped(self, shift: float) -> "DiscountCurve":
        """Parallel shift of all zero rates by `shift` (e.g. 1e-4 for 1bp)."""
        zeros = -self._log_dfs / self.times
        return DiscountCurve(self.times, np.exp(-(zeros + shift) * self.times))


def bootstrap_curve(
    deposits: list[tuple[float, float]],
    swaps: list[tuple[float, float]],
    fixed_freq: int = 1,
) -> DiscountCurve:
    """Bootstrap a discount curve from money-market deposits and par swaps.

    Parameters
    ----------
    deposits : list of (maturity_years, simple_rate) for the short end
    swaps : list of (maturity_years, par_rate); maturities must be whole
        multiples of the fixed-leg period and given in increasing order
    fixed_freq : fixed leg payments per year (1 = annual, 2 = semiannual)

    Each swap pillar is solved with a 1-D root find on its discount factor:
    interior payment dates past the previous pillar interpolate against the
    candidate pillar itself, so every input instrument reprices to zero NPV
    on the finished curve (not just approximately, as the naive
    "peel off the last df" recursion gives when payment dates fall between
    pillars).
    """
    if not deposits and not swaps:
        raise ValueError("need at least one instrument")

    times: list[float] = []
    dfs: list[float] = []

    for t, rate in sorted(deposits):
        if t <= 0:
            raise ValueError("deposit maturity must be positive")
        times.append(t)
        dfs.append(1.0 / (1.0 + rate * t))

    tau = 1.0 / fixed_freq
    for maturity, par in sorted(swaps):
        n_pay = round(maturity * fixed_freq)
        if abs(n_pay * tau - maturity) > 1e-9 or n_pay < 1:
            raise ValueError(
                f"swap maturity {maturity} not a multiple of the fixed period {tau}"
            )
        if not times:
            raise ValueError(
                "cannot bootstrap swap without shorter instruments covering "
                "its interior payment dates"
            )
        if maturity <= times[-1] + 1e-12:
            raise ValueError("swap pillars must extend the curve")

        pay_times = np.arange(1, n_pay + 1) * tau

        def par_swap_npv(df_n: float) -> float:
            candidate = DiscountCurve(
                np.array(times + [maturity]), np.array(dfs + [df_n])
            )
            annuity = float(np.sum(tau * candidate.df(pay_times)))
            return (1.0 - df_n) - par * annuity  # float leg - fixed leg

        df_n = _solve_bracketed(par_swap_npv, lo=1e-8, hi=1.5)
        times.append(maturity)
        dfs.append(df_n)

    return DiscountCurve(np.array(times), np.array(dfs))


def _solve_bracketed(f, lo: float, hi: float) -> float:
    """Brent root find with a validity check on the bracket."""
    from scipy.optimize import brentq

    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        raise ValueError("bootstrap failed: no discount factor solves this pillar")
    return float(brentq(f, lo, hi, xtol=1e-15))
