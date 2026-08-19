import numpy as np
import pytest

from quantdesk.futures.futures import (
    calendar_roll,
    fair_futures_price,
    implied_repo_rate,
)
from quantdesk.fx.fx import (
    forward_points,
    fx_forward,
    garman_kohlhagen,
    garman_kohlhagen_greeks,
)
from quantdesk.models.black_scholes import bs_price


SPOT, RD, RF, T, SIG = 1.10, 0.05, 0.03, 0.5, 0.10


def test_cip_forward():
    f = fx_forward(SPOT, RD, RF, T)
    assert f == pytest.approx(SPOT * np.exp((RD - RF) * T), abs=1e-15)
    assert f > SPOT  # domestic rate above foreign => forward premium


def test_forward_points_sign_flips_with_rate_differential():
    assert forward_points(SPOT, 0.05, 0.03, T) > 0
    assert forward_points(SPOT, 0.03, 0.05, T) < 0


def test_gk_is_bsm_with_foreign_rate_as_carry():
    gk = garman_kohlhagen(SPOT, 1.12, T, RD, RF, SIG, "call")
    bsm = bs_price(SPOT, 1.12, T, RD, RF, SIG, "call")
    assert gk == pytest.approx(bsm, abs=1e-15)


def test_gk_put_call_parity_through_forward():
    K = 1.08
    c = garman_kohlhagen(SPOT, K, T, RD, RF, SIG, "call")
    p = garman_kohlhagen(SPOT, K, T, RD, RF, SIG, "put")
    f = fx_forward(SPOT, RD, RF, T)
    assert c - p == pytest.approx(np.exp(-RD * T) * (f - K), abs=1e-12)


def test_atm_forward_straddle_symmetry():
    # Struck at the forward, call and put have equal value.
    f = fx_forward(SPOT, RD, RF, T)
    c = garman_kohlhagen(SPOT, f, T, RD, RF, SIG, "call")
    p = garman_kohlhagen(SPOT, f, T, RD, RF, SIG, "put")
    assert c == pytest.approx(p, abs=1e-12)


def test_gk_greeks_delta_range():
    g = garman_kohlhagen_greeks(SPOT, 1.10, T, RD, RF, SIG, "call")
    assert 0.4 < g.delta < 0.7
    assert g.gamma > 0 and g.vega > 0


def test_futures_fair_value_and_repo_round_trip():
    spot, r, q, t = 5000.0, 0.05, 0.015, 0.25
    f = fair_futures_price(spot, r, q, t)
    assert f > spot
    assert implied_repo_rate(spot, f, q, t) == pytest.approx(r, abs=1e-12)


def test_futures_backwardation_when_carry_negative():
    # Convenience yield above financing => futures below spot.
    assert fair_futures_price(80.0, 0.03, 0.08, 0.5) < 80.0


def test_calendar_roll_recovers_carry():
    spot, r, q = 100.0, 0.06, 0.01
    front = fair_futures_price(spot, r, q, 0.25)
    back = fair_futures_price(spot, r, q, 0.75)
    assert calendar_roll(front, back, 0.25, 0.75) == pytest.approx(r - q, abs=1e-12)


def test_input_validation():
    with pytest.raises(ValueError):
        fx_forward(-1.0, RD, RF, T)
    with pytest.raises(ValueError):
        implied_repo_rate(100.0, 101.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        calendar_roll(100.0, 101.0, 0.5, 0.25)
