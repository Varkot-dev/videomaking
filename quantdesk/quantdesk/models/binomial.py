"""Cox-Ross-Rubinstein binomial tree for European and American options.

Backward induction is vectorized across each tree layer, so a 2,000-step
tree prices in milliseconds without any compiled code.
"""

from __future__ import annotations

import numpy as np

from quantdesk.models.black_scholes import OptionKind


def crr_price(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    kind: OptionKind = "call",
    american: bool = False,
    steps: int = 800,
) -> float:
    """Price a vanilla option on a CRR tree.

    For European options this converges to Black-Scholes at O(1/steps);
    for American options it is the reference pricer in this library.
    """
    if steps < 1:
        raise ValueError("steps must be >= 1")
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        raise ValueError("S, K, sigma, T must be positive")

    dt = T / steps
    u = np.exp(sigma * np.sqrt(dt))
    d = 1.0 / u
    growth = np.exp((r - q) * dt)
    p = (growth - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError(
            f"risk-neutral probability {p:.4f} outside (0,1); "
            "increase steps or check r, q, sigma"
        )
    disc = np.exp(-r * dt)

    # Terminal spot prices S * u^j * d^(steps-j), j = 0..steps
    j = np.arange(steps + 1)
    spots = S * u**j * d ** (steps - j)
    if kind == "call":
        values = np.maximum(spots - K, 0.0)
    elif kind == "put":
        values = np.maximum(K - spots, 0.0)
    else:
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    for step in range(steps - 1, -1, -1):
        values = disc * (p * values[1:] + (1 - p) * values[:-1])
        if american:
            spots = S * u ** np.arange(step + 1) * d ** (step - np.arange(step + 1))
            exercise = spots - K if kind == "call" else K - spots
            values = np.maximum(values, exercise)

    return float(values[0])
