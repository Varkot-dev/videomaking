"""Implied volatility inversion.

Newton-Raphson using the analytic vega (quadratic convergence near the
solution) with a guaranteed Brent-bracketing fallback for deep ITM/OTM
quotes where vega is tiny and Newton stalls.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from quantdesk.models.black_scholes import OptionKind, bs_greeks, bs_price

_SIGMA_LO = 1e-6
_SIGMA_HI = 5.0


def _no_arb_bounds(S, K, T, r, q, kind: OptionKind) -> tuple[float, float]:
    df_r, df_q = np.exp(-r * T), np.exp(-q * T)
    if kind == "call":
        return max(S * df_q - K * df_r, 0.0), S * df_q
    return max(K * df_r - S * df_q, 0.0), K * df_r


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float = 0.0,
    kind: OptionKind = "call",
    tol: float = 1e-10,
    max_iter: int = 50,
) -> float:
    """Black vol that reproduces the observed option price.

    Raises ValueError if the price violates static no-arbitrage bounds.
    """
    if T <= 0:
        raise ValueError("T must be positive")
    lo, hi = _no_arb_bounds(S, K, T, r, q, kind)
    if not lo <= price <= hi:
        raise ValueError(
            f"price {price:.6g} outside no-arbitrage bounds [{lo:.6g}, {hi:.6g}]"
        )

    def objective(sigma: float) -> float:
        return float(bs_price(S, K, T, r, q, sigma, kind)) - price

    # Newton with vega, clamped to the bracket.
    sigma = 0.2
    for _ in range(max_iter):
        diff = objective(sigma)
        if abs(diff) < tol:
            return sigma
        vega = float(bs_greeks(S, K, T, r, q, sigma, kind).vega)
        if vega < 1e-12:
            break
        step = diff / vega
        sigma -= step
        if not _SIGMA_LO < sigma < _SIGMA_HI:
            break
        if abs(step) < 1e-14:
            return sigma

    # Brent fallback: bs_price is monotone in sigma, so the root is unique.
    f_lo, f_hi = objective(_SIGMA_LO), objective(_SIGMA_HI)
    if f_lo > 0 or f_hi < 0:
        # Price sits at (numerically) zero or max vol; return the boundary.
        return _SIGMA_LO if abs(f_lo) < abs(f_hi) else _SIGMA_HI
    return float(brentq(objective, _SIGMA_LO, _SIGMA_HI, xtol=1e-12))
