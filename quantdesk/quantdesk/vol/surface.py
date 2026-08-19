"""SVI volatility smile: the industry-standard single-expiry parameterization.

Gatheral's raw SVI writes total implied variance w = sigma^2 * T as a
function of log-moneyness k = ln(K/F):

    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + s^2))

Five parameters give the smile a level (a), wing slopes (b, rho), shift (m)
and curvature (s). Fitting is a bounded least-squares on observed
(strike, implied vol) quotes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares


@dataclass(frozen=True)
class SVISlice:
    """Fitted raw-SVI parameters for one expiry."""

    a: float
    b: float
    rho: float
    m: float
    s: float
    T: float
    forward: float

    def total_variance(self, k):
        """Total variance w(k) at log-moneyness k = ln(K/F)."""
        k = np.asarray(k, dtype=float)
        w = self.a + self.b * (
            self.rho * (k - self.m) + np.sqrt((k - self.m) ** 2 + self.s**2)
        )
        return float(w) if w.ndim == 0 else w

    def vol(self, strike):
        """Implied vol at the given strike(s)."""
        k = np.log(np.asarray(strike, dtype=float) / self.forward)
        out = np.sqrt(self.total_variance(k) / self.T)
        return float(out) if out.ndim == 0 else out

    def min_variance(self) -> float:
        """Minimum of w over k; must be positive for a valid surface."""
        return self.a + self.b * self.s * np.sqrt(max(1.0 - self.rho**2, 0.0))


def fit_svi(
    strikes: np.ndarray,
    vols: np.ndarray,
    T: float,
    forward: float,
) -> SVISlice:
    """Least-squares fit of a raw SVI slice to market implied vols.

    Bounds keep the slice statically sane: b >= 0, |rho| < 1, s > 0,
    and the fitted minimum total variance must be positive.
    """
    strikes = np.asarray(strikes, dtype=float)
    vols = np.asarray(vols, dtype=float)
    if strikes.shape != vols.shape or strikes.ndim != 1:
        raise ValueError("strikes and vols must be 1-D arrays of equal length")
    if len(strikes) < 5:
        raise ValueError("need at least 5 quotes to fit 5 parameters")
    if T <= 0 or forward <= 0:
        raise ValueError("T and forward must be positive")

    k = np.log(strikes / forward)
    w_mkt = vols**2 * T

    def residuals(params):
        a, b, rho, m, s = params
        w = a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s**2))
        return w - w_mkt

    w_atm = float(np.interp(0.0, k, w_mkt))
    x0 = [0.5 * w_atm, 0.1, -0.3, 0.0, 0.1]
    lb = [-1.0, 0.0, -0.999, -2.0, 1e-4]
    ub = [np.max(w_mkt) + 1.0, 10.0, 0.999, 2.0, 5.0]
    res = least_squares(residuals, x0, bounds=(lb, ub))

    slice_ = SVISlice(*res.x, T=T, forward=forward)
    if slice_.min_variance() <= 0:
        raise ValueError("fitted SVI slice has non-positive minimum variance")
    return slice_
