"""Benchmark: step columns from timings.json and the test profile default."""

from __future__ import annotations

import json

from manimgen.bench import runner, score
from manimgen.bench.cli import main

OK_LOG = (
    '[manimgen] Run summary for "T": 1 sections (1 ok)\n   1  section_01   ok   x\n'
)
USAGE = [{"role": "director", "duration_s": 100.5, "output_tokens": 1000}]

TIMINGS = {
    "total_seconds": 400.0,
    "profile": "draft",
    "resolution": "540x960",
    "fps": 30,
    "steps": [
        {"step": "director", "seconds": 60.0},
        {"step": "render", "seconds": 250.0},
        {"step": "fixes", "seconds": 40.0},
        {"step": "assembly", "seconds": 3.0},
    ],
}


def _make_run(root, wall="100", timings=None):
    root.mkdir(parents=True)
    (root / "run.log").write_text(OK_LOG)
    (root / "wall").write_text(wall)
    (root / "logs").mkdir()
    (root / "logs" / "llm_usage.jsonl").write_text(
        "\n".join(json.dumps(u) for u in USAGE)
    )
    (root / "videos").mkdir()
    if timings is not None:
        text = timings if isinstance(timings, str) else json.dumps(timings)
        (root / "logs" / "timings.json").write_text(text)
    return root


def test_score_run_reads_step_timings(tmp_path):
    d = _make_run(tmp_path / "a", "400", TIMINGS)
    r = score.score_run(d)
    assert (r["render_s"], r["fixes_s"], r["other_s"]) == (250.0, 40.0, 50.0)
    assert r["profile"] == "draft 540x960@30"


def test_older_runs_without_timings_still_score(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs / "v1" / "old", "10")
    _make_run(runs / "v1" / "new", "20", TIMINGS)
    res = score.score_variant("v1", runs)
    assert res["topics"]["old"]["render_s"] is None
    assert res["total"]["render_s"] == 250.0
    assert res["total"]["profile"] == "draft 540x960@30"
    md = score.render_markdown(res)
    for column in ("render s", "fixes s", "other s", "profile"):
        assert column in md
    cmp_md = score.render_comparison([res])
    assert "render s" in cmp_md and "draft 540x960@30" in cmp_md


def test_a_broken_timings_file_is_ignored(tmp_path):
    d = _make_run(tmp_path / "a", timings="{not json")
    assert score.score_run(d)["render_s"] is None


def test_bench_runs_the_test_profile_by_default(monkeypatch):
    assert "--draft" in runner.run_command(True)
    assert "--draft" not in runner.run_command(False)
    seen = []
    monkeypatch.setattr(runner, "run_all", lambda *a, **k: seen.append(k) or [])
    monkeypatch.setattr("manimgen.bench.cli._score", lambda args: 0)
    assert main(["run", "--only", "pythag"]) == 0
    assert main(["run", "--only", "pythag", "--final"]) == 0
    assert [k["draft"] for k in seen] == [True, False]
