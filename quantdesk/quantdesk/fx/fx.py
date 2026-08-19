"""FX forwards (covered interest parity) and FX options (Garman-Kohlhagen).

Quoting convention: spot S is domestic units per one foreign unit
(e.g. EURUSD = 1.10 means 1.10 USD per EUR, USD domestic, EUR foreign).
An FX option is then an equity-style option on S where the "dividend
yield" is the foreign interest rate: holding foreign currency earns r_f.
"""

from __future__ import annotations

from quantdesk.models.black_scholes import Greeks, OptionKind, bs_greeks, bs_price

import numpy as np


def fx_forward(spot: float, r_dom: float, r_for: float, T: float) -> float:
    """No-arbitrage forward rate from covered interest parity.

    F = S * exp((r_dom - r_for) * T). Continuous compounding.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")
    if T < 0:
        raise ValueError("T must be non-negative")
    return spot * np.exp((r_dom - r_for) * T)


def forward_points(spot: float, r_dom: float, r_for: float, T: float, pip: float = 1e-4) -> float:
    """Forward minus spot, quoted in pips (default pip = 0.0001)."""
    return (fx_forward(spot, r_dom, r_for, T) - spot) / pip


def garman_kohlhagen(
    spot: float,
    strike: float,
    T: float,
    r_dom: float,
    r_for: float,
    sigma: float,
    kind: OptionKind = "call",
):
    """Garman-Kohlhagen price of a European FX option.

    Exactly Black-Scholes-Merton with carry q = foreign rate. Price is in
    domestic currency per unit of foreign notional.
    """
    return bs_price(spot, strike, T, r_dom, r_for, sigma, kind)


def garman_kohlhagen_greeks(
    spot: float,
    strike: float,
    T: float,
    r_dom: float,
    r_for: float,
    sigma: float,
    kind: OptionKind = "call",
) -> Greeks:
    """Spot-delta Greeks of a European FX option."""
    return bs_greeks(spot, strike, T, r_dom, r_for, sigma, kind)
