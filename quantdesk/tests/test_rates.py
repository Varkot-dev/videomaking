import numpy as np
import pytest

from quantdesk.rates.curve import DiscountCurve, bootstrap_curve
from quantdesk.rates.swap import VanillaSwap


DEPOSITS = [(0.25, 0.045), (0.5, 0.046), (1.0, 0.047)]
SWAPS = [(2.0, 0.046), (3.0, 0.0455), (5.0, 0.045), (7.0, 0.0445), (10.0, 0.044)]


@pytest.fixture(scope="module")
def curve():
    return bootstrap_curve(DEPOSITS, SWAPS, fixed_freq=1)


def test_deposits_repriced_exactly(curve):
    for t, rate in DEPOSITS:
        assert curve.df(t) == pytest.approx(1.0 / (1.0 + rate * t), abs=1e-14)


def test_par_swaps_repriced_exactly(curve):
    for maturity, par in SWAPS:
        swap = VanillaSwap(notional=1.0, fixed_rate=par, maturity=maturity)
        assert swap.npv(curve) == pytest.approx(0.0, abs=1e-12)
        assert swap.par_rate(curve) == pytest.approx(par, abs=1e-12)


def test_discount_factors_decreasing(curve):
    ts = np.linspace(0.1, 12.0, 100)
    dfs = curve.df(ts)
    assert np.all(np.diff(dfs) < 0)
    assert curve.df(0.0) == pytest.approx(1.0)


def test_forward_rates_positive_and_sane(curve):
    for t1 in np.arange(0.5, 9.5, 0.5):
        f = curve.forward_rate(t1, t1 + 0.5)
        assert 0.0 < f < 0.10


def test_zero_rate_consistency(curve):
    t = 4.3
    assert curve.df(t) == pytest.approx(np.exp(-curve.zero_rate(t) * t), abs=1e-14)


def test_payer_swap_dv01_positive(curve):
    swap = VanillaSwap(notional=10_000_000, fixed_rate=0.045, maturity=5.0, side="payer")
    dv01 = swap.dv01(curve)
    # 5y annuity is roughly 4.4; 10mm * 4.4 * 1e-4 ~ 4.4k per bp.
    assert 3_000 < dv01 < 6_000
    receiver = VanillaSwap(
        notional=10_000_000, fixed_rate=0.045, maturity=5.0, side="receiver"
    )
    assert receiver.dv01(curve) == pytest.approx(-dv01, rel=1e-9)


def test_dv01_matches_annuity_approximation(curve):
    # For a par swap, DV01 ~= notional * annuity * 1bp.
    swap = VanillaSwap(notional=1e6, fixed_rate=0.045, maturity=5.0)
    approx = 1e6 * swap.annuity(curve) * 1e-4
    assert swap.dv01(curve) == pytest.approx(approx, rel=0.05)


def test_off_market_swap_signs(curve):
    par = VanillaSwap(1.0, 0.0, 5.0).par_rate(curve)
    payer_low = VanillaSwap(1.0, par - 0.005, 5.0, side="payer")
    payer_high = VanillaSwap(1.0, par + 0.005, 5.0, side="payer")
    assert payer_low.npv(curve) > 0  # paying below-market fixed is valuable
    assert payer_high.npv(curve) < 0


def test_bumped_curve_shifts_zeros(curve):
    up = curve.bumped(1e-4)
    for t in (1.0, 5.0, 10.0):
        assert up.zero_rate(t) - curve.zero_rate(t) == pytest.approx(1e-4, abs=1e-10)


def test_bootstrap_validation():
    with pytest.raises(ValueError):
        bootstrap_curve([], [])
    with pytest.raises(ValueError):
        bootstrap_curve(DEPOSITS, [(2.5, 0.046)], fixed_freq=1)  # not on grid
    with pytest.raises(ValueError):
        DiscountCurve(np.array([1.0, 0.5]), np.array([0.9, 0.95]))  # not increasing
