"""End-to-end demo: price a small multi-asset book and produce a risk report.

Run:  python examples/desk_report.py
"""

import numpy as np

from quantdesk import (
    VanillaSwap,
    bootstrap_curve,
    bs_greeks,
    bs_price,
    crr_price,
    fair_futures_price,
    fx_forward,
    garman_kohlhagen,
    implied_vol,
    mc_european,
)
from quantdesk.fx.fx import garman_kohlhagen_greeks
from quantdesk.risk.var import historical_es, historical_var, parametric_var
from quantdesk.vol.surface import fit_svi


def line(title=""):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}" if title else "-" * 62)


# ----------------------------------------------------------------- rates
line("RATES: USD single-curve bootstrap + 5y payer swap")
deposits = [(0.25, 0.0450), (0.50, 0.0460), (1.00, 0.0470)]
par_swaps = [(2.0, 0.0460), (3.0, 0.0455), (5.0, 0.0450), (7.0, 0.0445), (10.0, 0.0440)]
curve = bootstrap_curve(deposits, par_swaps)

print(f"{'t':>5} {'df':>9} {'zero':>7}")
for t in (0.5, 1, 2, 5, 10):
    print(f"{t:>5} {curve.df(t):>9.5f} {curve.zero_rate(t):>6.3%}")

swap = VanillaSwap(notional=10_000_000, fixed_rate=0.0430, maturity=5.0, side="payer")
print(f"\n5y payer swap, $10mm notional, fixed 4.30%")
print(f"  par rate : {swap.par_rate(curve):.4%}")
print(f"  NPV      : ${swap.npv(curve):>12,.0f}")
print(f"  DV01     : ${swap.dv01(curve):>12,.0f} per bp")

# --------------------------------------------------------------- options
line("EQUITY OPTIONS: SPX-style 6m 105% call")
S, K, T, r, q, sigma = 5000.0, 5250.0, 0.5, 0.045, 0.013, 0.16
px = bs_price(S, K, T, r, q, sigma, "call")
g = bs_greeks(S, K, T, r, q, sigma, "call")
amer = crr_price(S, K, T, r, q, sigma, "call", american=True, steps=1000)
mc = mc_european(S, K, T, r, q, sigma, "call", n_paths=400_000, seed=1)
print(f"  Black-Scholes : {px:10.2f}")
print(f"  CRR American  : {amer:10.2f}")
print(f"  Monte Carlo   : {mc.price:10.2f}  (stderr {mc.stderr:.3f}, "
      f"95% CI [{mc.ci()[0]:.2f}, {mc.ci()[1]:.2f}])")
print(f"  delta {g.delta:.3f}  gamma {g.gamma:.5f}  vega {g.vega:.1f}  "
      f"theta/day {g.theta / 365:.2f}")
print(f"  implied vol round-trip: {implied_vol(px, S, K, T, r, q, 'call'):.4%}")

# fit an SVI smile to a synthetic market
strikes = np.linspace(0.8, 1.2, 11) * S
k = np.log(strikes / (S * np.exp((r - q) * T)))
market_vols = 0.16 + 0.35 * k**2 - 0.12 * k
svi = fit_svi(strikes, market_vols, T, S * np.exp((r - q) * T))
print(f"  SVI fit: a={svi.a:.4f} b={svi.b:.3f} rho={svi.rho:.3f} "
      f"m={svi.m:.3f} s={svi.s:.3f}  (max err "
      f"{np.max(np.abs(svi.vol(strikes) - market_vols)):.2e})")

# --------------------------------------------------------------- futures
line("FUTURES: index carry")
fut = fair_futures_price(S, r, q, 0.25)
print(f"  3m fair value : {fut:.2f}  (spot {S:.0f}, carry {(r - q):.2%})")

# -------------------------------------------------------------------- fx
line("FX: EURUSD 6m forward + 25-delta-ish option")
spot, rd, rf, fx_sig = 1.10, 0.045, 0.030, 0.085
fwd = fx_forward(spot, rd, rf, 0.5)
opt = garman_kohlhagen(spot, 1.13, 0.5, rd, rf, fx_sig, "call")
fg = garman_kohlhagen_greeks(spot, 1.13, 0.5, rd, rf, fx_sig, "call")
print(f"  CIP forward   : {fwd:.5f}  ({(fwd - spot) / 1e-4:.1f} pips)")
print(f"  1.13 call     : {opt * 1e4:.1f} USD pips per EUR  (delta {fg.delta:.3f})")

# ------------------------------------------------------------------ risk
line("RISK: 1-day VaR of the option book (revaluation on simulated moves)")
rng = np.random.default_rng(0)
n_scen = 50_000
ret = rng.standard_t(df=5, size=n_scen) * 0.011  # fat-tailed daily index moves
book_today = bs_price(S, K, T, r, q, sigma, "call") * 100  # 100 contracts
book_scen = bs_price(S * (1 + ret), K, T - 1 / 252, r, q, sigma, "call") * 100
pnl = book_scen - book_today
print(f"  99% VaR (historical) : ${historical_var(pnl, 0.99):>10,.0f}")
print(f"  99% VaR (parametric) : ${parametric_var(pnl, 0.99):>10,.0f}")
print(f"  99% ES  (historical) : ${historical_es(pnl, 0.99):>10,.0f}")
line()
