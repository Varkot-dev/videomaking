import numpy as np
import pytest

from quantdesk.models.binomial import crr_price
from quantdesk.models.black_scholes import bs_price
from quantdesk.models.monte_carlo import mc_european


S, K, T, R, Q, SIG = 100.0, 105.0, 0.75, 0.04, 0.01, 0.25


@pytest.mark.parametrize("kind", ["call", "put"])
def test_crr_converges_to_black_scholes(kind):
    bs = bs_price(S, K, T, R, Q, SIG, kind)
    tree = crr_price(S, K, T, R, Q, SIG, kind, american=False, steps=2000)
    assert tree == pytest.approx(bs, abs=2e-3)


def test_american_worth_at_least_european():
    for kind in ("call", "put"):
        eur = crr_price(S, K, T, R, Q, SIG, kind, american=False, steps=500)
        amer = crr_price(S, K, T, R, Q, SIG, kind, american=True, steps=500)
        assert amer >= eur - 1e-12


def test_american_put_has_early_exercise_premium():
    # Deep ITM put with high rates: early exercise is clearly optimal.
    eur = crr_price(60, 100, 1.0, 0.10, 0.0, 0.2, "put", american=False, steps=500)
    amer = crr_price(60, 100, 1.0, 0.10, 0.0, 0.2, "put", american=True, steps=500)
    assert amer > eur + 0.5
    # And the American put is never below intrinsic.
    assert amer >= 40.0


def test_american_call_no_dividends_equals_european():
    # Merton: never optimal to exercise an American call on a non-dividend payer.
    eur = crr_price(S, K, T, R, 0.0, SIG, "call", american=False, steps=800)
    amer = crr_price(S, K, T, R, 0.0, SIG, "call", american=True, steps=800)
    assert amer == pytest.approx(eur, abs=1e-9)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_mc_within_confidence_interval(kind):
    bs = bs_price(S, K, T, R, Q, SIG, kind)
    res = mc_european(S, K, T, R, Q, SIG, kind, n_paths=400_000, seed=42)
    lo, hi = res.ci(z=3.5)
    assert lo <= bs <= hi
    assert res.stderr < 0.05


def test_variance_reduction_actually_reduces_variance():
    plain = mc_european(
        S, K, T, R, Q, SIG, "call",
        n_paths=100_000, antithetic=False, control_variate=False, seed=7,
    )
    reduced = mc_european(
        S, K, T, R, Q, SIG, "call",
        n_paths=100_000, antithetic=True, control_variate=True, seed=7,
    )
    assert reduced.stderr < 0.5 * plain.stderr


def test_pathwise_greeks_match_analytic():
    from quantdesk.models.black_scholes import bs_greeks

    g = bs_greeks(S, K, T, R, Q, SIG, "call")
    res = mc_european(S, K, T, R, Q, SIG, "call", n_paths=500_000, seed=11)
    assert res.delta == pytest.approx(g.delta, abs=5e-3)
    assert res.vega == pytest.approx(g.vega, rel=2e-2)


def test_mc_reproducible_with_seed():
    a = mc_european(S, K, T, R, Q, SIG, "call", n_paths=50_000, seed=3)
    b = mc_european(S, K, T, R, Q, SIG, "call", n_paths=50_000, seed=3)
    assert a.price == b.price
