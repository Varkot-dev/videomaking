import numpy as np
import pytest

from quantdesk.models.black_scholes import bs_greeks, bs_price


S, K, T, R, Q, SIG = 100.0, 100.0, 1.0, 0.05, 0.02, 0.2


def test_known_value_call():
    # Reference value computed independently (Haug tables / verified impl).
    assert bs_price(100, 100, 1.0, 0.05, 0.0, 0.2, "call") == pytest.approx(
        10.4506, abs=1e-4
    )


def test_put_call_parity():
    c = bs_price(S, K, T, R, Q, SIG, "call")
    p = bs_price(S, K, T, R, Q, SIG, "put")
    parity = S * np.exp(-Q * T) - K * np.exp(-R * T)
    assert c - p == pytest.approx(parity, abs=1e-12)


def test_monotone_in_vol_and_positive():
    vols = np.linspace(0.05, 1.0, 50)
    prices = bs_price(S, K, T, R, Q, vols, "call")
    assert np.all(np.diff(prices) > 0)
    assert np.all(prices > 0)


def test_zero_time_is_intrinsic():
    assert bs_price(110, 100, 0.0, R, Q, SIG, "call") == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, R, Q, SIG, "put") == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, R, Q, SIG, "call") == 0.0


def test_zero_vol_is_discounted_forward_intrinsic():
    fwd = S * np.exp((R - Q) * T)
    expect = np.exp(-R * T) * max(fwd - K, 0.0)
    assert bs_price(S, K, T, R, Q, 0.0, "call") == pytest.approx(expect, abs=1e-12)


def test_vectorized_matches_scalar():
    strikes = np.array([80.0, 100.0, 120.0])
    vec = bs_price(S, strikes, T, R, Q, SIG, "put")
    scal = [bs_price(S, k, T, R, Q, SIG, "put") for k in strikes]
    np.testing.assert_allclose(vec, scal, rtol=0, atol=1e-14)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_greeks_match_finite_differences(kind):
    g = bs_greeks(S, K, T, R, Q, SIG, kind)
    h = 1e-4

    def p(s=S, k=K, t=T, r=R, q=Q, sig=SIG):
        return bs_price(s, k, t, r, q, sig, kind)

    assert g.delta == pytest.approx((p(s=S + h) - p(s=S - h)) / (2 * h), abs=1e-6)
    assert g.gamma == pytest.approx(
        (p(s=S + h) - 2 * p() + p(s=S - h)) / h**2, abs=1e-4
    )
    assert g.vega == pytest.approx(
        (p(sig=SIG + h) - p(sig=SIG - h)) / (2 * h), abs=1e-4
    )
    assert g.rho == pytest.approx((p(r=R + h) - p(r=R - h)) / (2 * h), abs=1e-4)
    # theta is -dV/dt (calendar time forward = expiry shrinking)
    assert g.theta == pytest.approx(-(p(t=T + h) - p(t=T - h)) / (2 * h), abs=1e-4)


def test_call_delta_bounds():
    deltas = bs_greeks(S, np.linspace(50, 200, 40), T, R, Q, SIG, "call").delta
    assert np.all(deltas > 0) and np.all(deltas < 1)
    assert np.all(np.diff(deltas) < 0)  # decreasing in strike


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        bs_price(-1, K, T, R, Q, SIG)
    with pytest.raises(ValueError):
        bs_price(S, K, -0.1, R, Q, SIG)
    with pytest.raises(ValueError):
        bs_price(S, K, T, R, Q, SIG, kind="straddle")
    with pytest.raises(ValueError):
        bs_greeks(S, K, 0.0, R, Q, SIG)
