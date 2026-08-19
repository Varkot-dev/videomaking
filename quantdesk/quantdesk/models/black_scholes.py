"""Black-Scholes-Merton pricing and analytic Greeks.

All functions are vectorized: scalar or ndarray inputs broadcast together.
The continuous carry yield ``q`` makes this the single pricing kernel for
equity options (q = dividend yield), FX options (q = foreign rate,
Garman-Kohlhagen), and options on futures (q = r, Black-76).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.stats import norm

OptionKind = Literal["call", "put"]

_EPS_T = 1e-12


def _d1_d2(S, K, T, r, q, sigma):
    sqrtT = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return d1, d2


def _validate(S, K, T, sigma):
    if np.any(np.asarray(S) <= 0) or np.any(np.asarray(K) <= 0):
        raise ValueError("spot and strike must be positive")
    if np.any(np.asarray(T) < 0):
        raise ValueError("time to expiry must be non-negative")
    if np.any(np.asarray(sigma) < 0):
        raise ValueError("volatility must be non-negative")


def bs_price(S, K, T, r, q, sigma, kind: OptionKind = "call"):
    """Black-Scholes-Merton price of a European option.

    Parameters
    ----------
    S : spot price
    K : strike
    T : time to expiry in years
    r : continuously compounded risk-free (domestic) rate
    q : continuous carry yield (dividend yield / foreign rate / r for futures)
    sigma : Black volatility
    kind : "call" or "put"

    Degenerate cases (T = 0 or sigma = 0) return discounted intrinsic value,
    which is the correct limit of the formula.
    """
    _validate(S, K, T, sigma)
    S, K, T, r, q, sigma = np.broadcast_arrays(
        *np.atleast_1d(S, K, T, r, q, sigma), subok=False
    )
    S, K, T, r, q, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, r, q, sigma))

    price = np.empty_like(S)
    degenerate = (T <= _EPS_T) | (sigma <= _EPS_T)

    # Deterministic limit: forward is known, payoff is discounted intrinsic on it.
    fwd = S * np.exp((r - q) * T)
    disc = np.exp(-r * T)
    if kind == "call":
        intrinsic = disc * np.maximum(fwd - K, 0.0)
    elif kind == "put":
        intrinsic = disc * np.maximum(K - fwd, 0.0)
    else:
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
    price[degenerate] = intrinsic[degenerate]

    live = ~degenerate
    if np.any(live):
        d1, d2 = _d1_d2(S[live], K[live], T[live], r[live], q[live], sigma[live])
        df_q = np.exp(-q[live] * T[live])
        df_r = np.exp(-r[live] * T[live])
        if kind == "call":
            price[live] = S[live] * df_q * norm.cdf(d1) - K[live] * df_r * norm.cdf(d2)
        else:
            price[live] = K[live] * df_r * norm.cdf(-d2) - S[live] * df_q * norm.cdf(-d1)

    return price[0] if price.shape == (1,) else price


@dataclass(frozen=True)
class Greeks:
    """First and second order sensitivities of a European option.

    theta is per year (divide by 365 for a daily theta); vega and rho are
    per unit change (multiply by 0.01 for per-vol-point / per-100bp).
    """

    delta: float | np.ndarray
    gamma: float | np.ndarray
    vega: float | np.ndarray
    theta: float | np.ndarray
    rho: float | np.ndarray


def bs_greeks(S, K, T, r, q, sigma, kind: OptionKind = "call") -> Greeks:
    """Analytic Black-Scholes-Merton Greeks (spot Greeks, carry-adjusted)."""
    _validate(S, K, T, sigma)
    S, K, T, r, q, sigma = np.broadcast_arrays(
        *np.atleast_1d(S, K, T, r, q, sigma), subok=False
    )
    S, K, T, r, q, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, r, q, sigma))
    if np.any((T <= _EPS_T) | (sigma <= _EPS_T)):
        raise ValueError("Greeks are undefined at T=0 or sigma=0; bump inputs instead")

    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    sqrtT = np.sqrt(T)
    df_q = np.exp(-q * T)
    df_r = np.exp(-r * T)
    pdf_d1 = norm.pdf(d1)

    gamma = df_q * pdf_d1 / (S * sigma * sqrtT)
    vega = S * df_q * pdf_d1 * sqrtT

    if kind == "call":
        delta = df_q * norm.cdf(d1)
        theta = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrtT)
            - r * K * df_r * norm.cdf(d2)
            + q * S * df_q * norm.cdf(d1)
        )
        rho = K * T * df_r * norm.cdf(d2)
    elif kind == "put":
        delta = -df_q * norm.cdf(-d1)
        theta = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrtT)
            + r * K * df_r * norm.cdf(-d2)
            - q * S * df_q * norm.cdf(-d1)
        )
        rho = -K * T * df_r * norm.cdf(-d2)
    else:
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    def _squeeze(x):
        return x[0] if x.shape == (1,) else x

    return Greeks(*(_squeeze(g) for g in (delta, gamma, vega, theta, rho)))
