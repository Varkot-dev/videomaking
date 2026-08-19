import numpy as np
import pytest

from quantdesk.models.black_scholes import bs_greeks, bs_price
from quantdesk.risk.var import (
    historical_es,
    historical_var,
    parametric_es,
    parametric_var,
)
from quantdesk.vol.implied import implied_vol
from quantdesk.vol.surface import fit_svi


S, K, T, R, Q = 100.0, 100.0, 1.0, 0.05, 0.02


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("sigma", [0.05, 0.2, 0.8])
@pytest.mark.parametrize("strike", [70.0, 100.0, 140.0])
def test_implied_vol_round_trip(kind, sigma, strike):
    price = bs_price(S, strike, T, R, Q, sigma, kind)
    iv = implied_vol(price, S, strike, T, R, Q, kind)
    # Price-space round trip is the well-posed check: it must always hold.
    assert bs_price(S, strike, T, R, Q, iv, kind) == pytest.approx(price, abs=1e-8)
    # Vol-space accuracy is only meaningful where vega is non-negligible;
    # deep ITM/OTM at low vol the price-to-vol map is ill-conditioned.
    vega = bs_greeks(S, strike, T, R, Q, sigma, kind).vega
    if vega > 1e-2:
        assert iv == pytest.approx(sigma, abs=1e-6)


def test_implied_vol_rejects_arbitrageable_price():
    with pytest.raises(ValueError):
        implied_vol(200.0, S, K, T, R, Q, "call")  # above S*e^{-qT}
    with pytest.raises(ValueError):
        implied_vol(-0.5, S, K, T, R, Q, "call")


def test_svi_fit_recovers_smile():
    # Generate a smile from known SVI parameters, fit, and compare vols.
    from quantdesk.vol.surface import SVISlice

    true = SVISlice(a=0.02, b=0.4, rho=-0.4, m=0.05, s=0.15, T=0.5, forward=100.0)
    strikes = np.linspace(70, 140, 15)
    vols = true.vol(strikes)
    fitted = fit_svi(strikes, vols, T=0.5, forward=100.0)
    np.testing.assert_allclose(fitted.vol(strikes), vols, atol=5e-4)
    assert fitted.min_variance() > 0


def test_svi_fit_noisy_market_smile():
    rng = np.random.default_rng(0)
    strikes = np.linspace(80, 125, 12)
    k = np.log(strikes / 100.0)
    vols = 0.2 + 0.3 * k**2 - 0.1 * k + rng.normal(0, 5e-4, len(k))
    fitted = fit_svi(strikes, vols, T=0.25, forward=100.0)
    assert np.max(np.abs(fitted.vol(strikes) - vols)) < 5e-3


def test_var_on_normal_pnl_matches_theory():
    rng = np.random.default_rng(1)
    mu, sd = 0.0, 1_000.0
    pnl = rng.normal(mu, sd, 200_000)
    from scipy.stats import norm

    theory = -norm.ppf(0.01) * sd  # ~2326
    assert historical_var(pnl, 0.99) == pytest.approx(theory, rel=0.03)
    assert parametric_var(pnl, 0.99) == pytest.approx(theory, rel=0.03)


def test_es_exceeds_var():
    rng = np.random.default_rng(2)
    pnl = rng.standard_t(df=4, size=100_000) * 500.0
    assert historical_es(pnl, 0.99) > historical_var(pnl, 0.99)
    assert parametric_es(pnl, 0.99) > parametric_var(pnl, 0.99)


def test_fat_tails_show_up_in_historical_not_parametric():
    # Student-t P&L: historical ES should exceed the normal-model ES.
    rng = np.random.default_rng(3)
    pnl = rng.standard_t(df=3, size=200_000)
    assert historical_es(pnl, 0.99) > parametric_es(pnl, 0.99)


def test_var_input_validation():
    with pytest.raises(ValueError):
        historical_var(np.array([1.0]), 0.99)
    with pytest.raises(ValueError):
        parametric_var(np.random.default_rng(0).normal(size=100), alpha=0.4)
