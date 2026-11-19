"""Benchmark harness: a fixed set of Short topics and an automatic scorecard.

``python -m manimgen.bench run`` plans and renders the topics, ``score`` reads
the run folders into a table, ``sheet`` tiles frames for human review.
"""

from __future__ import annotations

from pathlib import Path

#: Folder with ``topics.yaml``; run output goes to ``<BENCH_DIR>/runs`` (git-ignored).
BENCH_DIR = Path(__file__).resolve().parents[2] / "bench"
RUNS_DIR = BENCH_DIR / "runs"
TOPICS_FILE = BENCH_DIR / "topics.yaml"
