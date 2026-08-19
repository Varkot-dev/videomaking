"""Portfolio VaR and Expected Shortfall.

Two standard estimators over a P&L (or returns) sample:

- historical simulation: the empirical quantile of realized P&L
- parametric (variance-covariance): normal approximation from the
  sample mean and standard deviation

Both are reported as positive loss numbers at confidence level alpha
(e.g. alpha = 0.99 gives the loss exceeded 1% of the time).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def _check(pnl: np.ndarray, alpha: float) -> np.ndarray:
    pnl = np.asarray(pnl, dtype=float)
    if pnl.ndim != 1 or len(pnl) < 2:
        raise ValueError("pnl must be a 1-D array with at least 2 observations")
    if not 0.5 < alpha < 1.0:
        raise ValueError("alpha must be in (0.5, 1)")
    return pnl


def historical_var(pnl, alpha: float = 0.99) -> float:
    """Empirical VaR: the (1-alpha) quantile of P&L, sign-flipped to a loss."""
    pnl = _check(pnl, alpha)
    return float(-np.quantile(pnl, 1.0 - alpha))


def historical_es(pnl, alpha: float = 0.99) -> float:
    """Expected Shortfall: mean loss beyond the VaR threshold."""
    pnl = _check(pnl, alpha)
    var = -historical_var(pnl, alpha)
    tail = pnl[pnl <= var]
    if len(tail) == 0:
        return historical_var(pnl, alpha)
    return float(-tail.mean())


def parametric_var(pnl, alpha: float = 0.99) -> float:
    """Delta-normal VaR from sample mean and volatility."""
    pnl = _check(pnl, alpha)
    mu, sd = pnl.mean(), pnl.std(ddof=1)
    return float(-(mu + sd * norm.ppf(1.0 - alpha)))


def parametric_es(pnl, alpha: float = 0.99) -> float:
    """Expected Shortfall under the normal model:
    ES = -(mu - sd * phi(z_{1-alpha}) / (1-alpha))."""
    pnl = _check(pnl, alpha)
    mu, sd = pnl.mean(), pnl.std(ddof=1)
    z = norm.ppf(1.0 - alpha)
    return float(-(mu - sd * norm.pdf(z) / (1.0 - alpha)))
