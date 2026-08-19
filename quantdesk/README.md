# quantdesk

A multi-asset derivatives pricing and risk engine in Python/NumPy, built from
first principles: **options, futures, interest rate swaps, and FX** priced off
shared, tested numerical kernels — with the verification discipline of
production pricing code (65 tests: parity identities, cross-model convergence,
exact repricing of curve inputs, Greeks vs. finite differences).

```
pip install -e .
python -m pytest            # 65 tests, ~1.5s
python examples/desk_report.py
python benchmarks/bench_mc.py
```

## What's inside

| Asset class | Module | Models |
|---|---|---|
| Equity/index options | `models/` | Black–Scholes–Merton (vectorized, analytic Greeks), CRR binomial (American/European), Monte Carlo (antithetic + control variates, pathwise Greeks) |
| Volatility | `vol/` | Implied vol (Newton with vega + guaranteed Brent fallback, no-arbitrage bounds), Gatheral raw-SVI smile fit |
| Interest rate swaps | `rates/` | Discount curve bootstrap (deposits + par swaps, root-solved pillars, log-linear DF interpolation ⇒ piecewise-flat forwards), vanilla IRS: NPV, par rate, annuity, DV01 |
| Futures/forwards | `futures/` | Cost-of-carry fair value, implied repo, basis, calendar roll |
| FX | `fx/` | Covered-interest-parity forwards and points, Garman–Kohlhagen options + Greeks |
| Risk | `risk/` | Historical & parametric VaR and Expected Shortfall; full-revaluation scenario P&L in the demo |

One design decision ties it together: a single Black kernel with a continuous
carry yield `q` prices equities (`q` = dividend yield), FX (`q` = foreign
rate — Garman–Kohlhagen), and futures options (`q = r` — Black-76), so every
downstream model inherits the same tested Greeks and edge-case handling.

## Verification philosophy

Pricing code is only as good as the invariants it's tested against. Every
number this library produces is pinned down by at least one independent check:

| Claim | Test |
|---|---|
| BSM prices are right | Reference values + put–call parity to 1e-12 + monotonicity in vol |
| Analytic Greeks are right | Central finite differences of the price function |
| Binomial tree is right | Converges to BSM (European); American ≥ European; Merton's no-early-exercise theorem for calls without dividends |
| Monte Carlo is right | True price inside the estimator's own confidence interval; pathwise delta/vega match analytic Greeks |
| Variance reduction works | Antithetic + control variates must at least halve the standard error (pairs collapsed before the CV regression, so the stderr is honest) |
| Curve bootstrap is right | Every input deposit and par swap **reprices to zero NPV to 1e-12** on the finished curve |
| Swap DV01 is right | Sign flips payer/receiver; magnitude matches the annuity approximation |
| Implied vol is right | Round-trips in price space always; in vol space wherever vega makes the problem well-conditioned |
| FX pricing is right | Put–call parity through the CIP forward; ATM-forward straddle symmetry |
| VaR is right | Matches the closed-form normal quantile on Gaussian P&L; ES > VaR; fat tails show up in historical but not parametric ES |

Two of these encode numerical judgment calls worth knowing about:

- **Bootstrap pillars are root-solved, not peeled.** The textbook recursion
  (`df_n = (1 − par·A)/(1 + par·τ)`) silently misprices later swaps whose
  interior payment dates fall between pillars, because the interpolated
  discount factor changes once the new pillar is added. Each pillar here is
  solved with Brent against the candidate curve *including itself*, so inputs
  reprice exactly.
- **Implied vol accuracy is stated in the right space.** Deep ITM/OTM at low
  vol, vega → 0 and the price→vol map is ill-conditioned; no solver can
  recover vol to 1e-7 there. The tests demand price-space round-trip always,
  vol-space accuracy only where vega is non-negligible.

## Performance

Vectorized NumPy throughout (no Python loops over paths or strikes). On a
modest container:

```
Black-Scholes, 1,000,000 strikes (vectorized)      ~140 ms
Monte Carlo, 1,000,000 paths (anti + CV)            ~40 ms
CRR binomial, 2,000 steps (American put)            ~60 ms
```

## Example output

`examples/desk_report.py` prices a small book end-to-end — bootstrap a USD
curve and risk a $10mm 5y payer swap, price an SPX-style call three ways
(closed-form / tree / MC with CI), fit an SVI smile, compute an FX forward and
Garman–Kohlhagen option, and run a full-revaluation 99% VaR/ES on fat-tailed
scenarios:

```
5y payer swap, $10mm notional, fixed 4.30%
  par rate : 4.5000%
  NPV      : $      87,701
  DV01     : $       4,563 per bp

  Black-Scholes :     154.53
  CRR American  :     154.53
  Monte Carlo   :     154.30  (stderr 0.099, 95% CI [154.11, 154.49])

  99% VaR (historical) : $     6,381
  99% ES  (historical) : $     7,819
```

## Scope and roadmap

Deliberately single-curve and continuous-compounding: the goal is correct,
verifiable core models, not calendar plumbing. Natural extensions, roughly in
order of interest:

- OIS/projection multi-curve framework with tenor basis
- Day-count conventions and date rolls (ACT/360, 30/360, modified following)
- Longstaff–Schwartz American Monte Carlo
- SABR smile + comparison with SVI on the same quotes
- Key-rate DV01s and curve risk bucketing
