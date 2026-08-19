"""Timing benchmark: vectorized Monte Carlo and binomial pricing throughput.

Run:  python benchmarks/bench_mc.py
"""

import time

import numpy as np

from quantdesk.models.binomial import crr_price
from quantdesk.models.black_scholes import bs_price
from quantdesk.models.monte_carlo import mc_european


def timeit(label, fn, repeats=5):
    best = min(_time_once(fn) for _ in range(repeats))
    print(f"  {label:<44} {best * 1e3:9.2f} ms")
    return best


def _time_once(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


if __name__ == "__main__":
    S, K, T, r, q, sig = 100.0, 105.0, 1.0, 0.05, 0.01, 0.2

    print("quantdesk benchmarks (best of 5)")
    timeit(
        "Black-Scholes, 1,000,000 strikes (vectorized)",
        lambda: bs_price(S, np.linspace(50, 200, 1_000_000), T, r, q, sig),
    )
    timeit(
        "Monte Carlo, 1,000,000 paths (anti + CV)",
        lambda: mc_european(S, K, T, r, q, sig, n_paths=1_000_000, seed=0),
    )
    timeit(
        "CRR binomial, 2,000 steps (American put)",
        lambda: crr_price(S, K, T, r, q, sig, "put", american=True, steps=2_000),
    )
