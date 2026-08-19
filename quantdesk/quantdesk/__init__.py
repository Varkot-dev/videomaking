"""quantdesk: multi-asset derivatives pricing and risk engine.

Covers the four core linear/nonlinear derivative families:

- Equity/index options  (Black-Scholes-Merton, CRR binomial, Monte Carlo)
- Futures & forwards    (cost-of-carry, implied repo, basis)
- Interest rate swaps   (bootstrapped discount curve, par rates, DV01)
- FX derivatives        (covered interest parity forwards, Garman-Kohlhagen)

plus implied volatility (Newton + Brent), an SVI smile fit, and
portfolio-level risk (aggregated Greeks, parametric and historical VaR/ES).
"""

from quantdesk.models.black_scholes import bs_price, bs_greeks, Greeks
from quantdesk.models.binomial import crr_price
from quantdesk.models.monte_carlo import mc_european, MCResult
from quantdesk.vol.implied import implied_vol
from quantdesk.rates.curve import DiscountCurve, bootstrap_curve
from quantdesk.rates.swap import VanillaSwap
from quantdesk.fx.fx import fx_forward, garman_kohlhagen
from quantdesk.futures.futures import fair_futures_price, implied_repo_rate

__all__ = [
    "bs_price",
    "bs_greeks",
    "Greeks",
    "crr_price",
    "mc_european",
    "MCResult",
    "implied_vol",
    "DiscountCurve",
    "bootstrap_curve",
    "VanillaSwap",
    "fx_forward",
    "garman_kohlhagen",
    "fair_futures_price",
    "implied_repo_rate",
]

__version__ = "0.1.0"
