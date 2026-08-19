"""Futures and forwards: cost-of-carry fair value, implied repo, basis.

Under deterministic rates, futures and forward prices coincide, so a single
carry model covers index futures (q = dividend yield), commodity futures
(q = convenience yield - storage cost), and bond/repo economics.
"""

from __future__ import annotations

import numpy as np


def fair_futures_price(spot: float, r: float, q: float, T: float) -> float:
    """Cost-of-carry fair value F = S * exp((r - q) * T).

    q is the net benefit of holding the underlying: dividend yield for
    index futures, convenience yield net of storage for commodities.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")
    if T < 0:
        raise ValueError("T must be non-negative")
    return spot * np.exp((r - q) * T)


def implied_repo_rate(spot: float, futures: float, q: float, T: float) -> float:
    """Financing rate implied by an observed futures price.

    Inverts F = S * exp((r - q) T) for r. Comparing this to actual funding
    identifies rich/cheap basis.
    """
    if spot <= 0 or futures <= 0:
        raise ValueError("prices must be positive")
    if T <= 0:
        raise ValueError("T must be positive")
    return np.log(futures / spot) / T + q


def basis(spot: float, futures: float) -> float:
    """Raw basis: futures price minus spot."""
    return futures - spot


def calendar_roll(front: float, back: float, T_front: float, T_back: float) -> float:
    """Annualized implied carry between two futures expiries.

    The rate at which the curve rolls between contracts; positive means
    contango financing, negative means backwardation.
    """
    if T_back <= T_front:
        raise ValueError("need T_back > T_front")
    return np.log(back / front) / (T_back - T_front)
