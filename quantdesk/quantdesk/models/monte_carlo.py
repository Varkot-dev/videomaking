"""Monte Carlo pricing of European options under GBM.

Demonstrates the two variance-reduction techniques every desk MC uses:

- antithetic variates: each normal draw Z is paired with -Z
- control variate: the terminal spot S_T, whose expectation under the
  risk-neutral measure is the forward S*exp((r-q)T), is used to strip
  correlated noise out of the payoff estimator

Also computes pathwise delta and vega (derivative of the payoff along each
path), which are unbiased and far cheaper than bump-and-reprice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from quantdesk.models.black_scholes import OptionKind


@dataclass(frozen=True)
class MCResult:
    price: float
    stderr: float
    delta: float
    vega: float
    n_paths: int

    def ci(self, z: float = 1.96) -> tuple[float, float]:
        """Confidence interval for the price at the given z-score."""
        return (self.price - z * self.stderr, self.price + z * self.stderr)


def mc_european(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    kind: OptionKind = "call",
    n_paths: int = 200_000,
    antithetic: bool = True,
    control_variate: bool = True,
    seed: int | None = None,
) -> MCResult:
    """Price a European option by simulating S_T directly (exact GBM step).

    Returns price with standard error, plus pathwise delta and vega.
    """
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        raise ValueError("S, K, sigma, T must be positive")
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    rng = np.random.default_rng(seed)
    if antithetic:
        n_paths += n_paths % 2  # need whole pairs
        half = n_paths // 2
        z = rng.standard_normal(half)
        z = np.concatenate([z, -z])
    else:
        z = rng.standard_normal(n_paths)

    sqrtT = np.sqrt(T)
    drift = (r - q - 0.5 * sigma**2) * T
    ST = S * np.exp(drift + sigma * sqrtT * z)
    disc = np.exp(-r * T)

    sign = 1.0 if kind == "call" else -1.0
    payoff = disc * np.maximum(sign * (ST - K), 0.0)

    # Paired draws (Z, -Z) are dependent: collapse each pair to its mean
    # first, so the remaining samples are i.i.d. The control-variate beta
    # is then estimated on those samples (estimating it on the raw
    # dependent pairs gives a beta that is suboptimal for the paired
    # estimator and a biased standard error).
    if antithetic:
        samples = 0.5 * (payoff[:half] + payoff[half:])
        control = 0.5 * disc * (ST[:half] + ST[half:])
    else:
        samples = payoff
        control = disc * ST

    if control_variate:
        # E[disc * ST] = S * exp(-q T): discounted spot is a zero-cost control.
        control_mean = S * np.exp(-q * T)
        cov = np.cov(samples, control, ddof=1)
        beta = cov[0, 1] / cov[1, 1]
        samples = samples - beta * (control - control_mean)

    price = float(samples.mean())
    stderr = float(samples.std(ddof=1) / np.sqrt(len(samples)))

    # Pathwise Greeks: d payoff/dS = disc * 1{ITM} * sign * ST/S, etc.
    itm = sign * (ST - K) > 0
    delta = float(np.mean(disc * itm * sign * ST / S))
    dST_dsigma = ST * (-sigma * T + sqrtT * z)
    vega = float(np.mean(disc * itm * sign * dST_dsigma))

    return MCResult(price=price, stderr=stderr, delta=delta, vega=vega, n_paths=n_paths)
