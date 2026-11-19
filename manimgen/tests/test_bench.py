"""Benchmark harness: scorer and frame sheet on synthetic run folders. No LLM, no render."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from manimgen.bench import score, sheet
from manimgen.bench.cli import main
from manimgen.bench.topics import load_topics

OK_LOG = '[manimgen] Run summary for "T": 1 sections (1 ok)\n   1  section_01   ok   x\nexit=0\n'
RETRY_LOG = (
    "[retry] Attempt 1/3 rendered but has visual defects:\n"
    "[retry] Requesting visual fix from LLM...\n"
    "[retry] Attempt 2/3 rendered but has visual defects:\n"
    "[retry] Requesting visual fix from LLM...\n"
    '[manimgen] Run summary for "T": 1 sections (1 accepted_with_defects)\n'
)


def _make_run(root, log, wall="100", usage=(), overlaps=None):
    root.mkdir(parents=True)
    (root / "run.log").write_text(log)
    (root / "wall").write_text(wall)
    (root / "logs").mkdir()
    (root / "logs" / "llm_usage.jsonl").write_text(
        "\n".join(json.dumps(u) for u in usage)
    )
    (root / "videos").mkdir()
    if overlaps is not None:
        (root / "videos" / "Section01Scene.mp4.overlaps.json").write_text(
            json.dumps(overlaps)
        )
    return root


USAGE = [
    {"role": "director", "duration_s": 100.5, "output_tokens": 1000},
    {"role": "director", "duration_s": 50, "output_tokens": 500},
    {"role": "layout_checker", "duration_s": 10, "output_tokens": 5},
]
PROBE = {
    "findings": [{"kind": "overlap"}, {"kind": "unsafe"}, {"kind": "unsafe"}],
    "nudges": 2,
}


def test_parse_log_ok_first_try():
    r = score.parse_log(OK_LOG)
    assert r["outcome"] == "ok" and r["first_try"] and r["renders"] == 1


def test_parse_log_defects_and_renders():
    r = score.parse_log(RETRY_LOG)
    assert r["outcome"] == "accepted_with_defects"
    assert not r["first_try"]
    assert r["renders"] == 3


def test_parse_log_fallback_wins_and_missing_summary():
    log = 'Run summary for "T": 2 sections (1 ok, 1 fallback)\n'
    assert score.parse_log(log)["outcome"] == "fallback"
    assert score.parse_log("crashed")["outcome"] == "no_summary"


def test_score_run_reads_everything(tmp_path):
    d = _make_run(tmp_path / "a", RETRY_LOG, "321", USAGE, PROBE)
    r = score.score_run(d)
    assert r["wall_s"] == 321
    assert r["director_s"] == 150.5
    assert r["director_tokens"] == 1500
    assert r["other_llm_s"] == 10
    assert (r["overlap"], r["offscreen"], r["unsafe"], r["nudges"]) == (1, 0, 2, 2)


def test_score_run_tolerates_missing_files(tmp_path):
    (tmp_path / "run.log").write_text(OK_LOG)
    r = score.score_run(tmp_path)
    assert r["wall_s"] is None and r["director_s"] == 0 and r["overlap"] == 0


def test_variant_layouts_and_markdown(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs / "v1" / "one", OK_LOG, "10", USAGE)
    _make_run(runs / "v1" / "two", RETRY_LOG, "30", USAGE, PROBE)
    res = score.score_variant("v1", runs)
    assert set(res["topics"]) == {"one", "two"}
    assert res["total"]["wall_s"] == 40
    assert res["total"]["ok"] == 1 and res["total"]["accepted_with_defects"] == 1
    assert res["total"]["first_try"] == 1
    md = score.render_markdown(res)
    assert "| one |" in md and "total (2)" in md

    exp = tmp_path / "exp"
    _make_run(exp / "one" / "v2", OK_LOG, "5", USAGE)
    res2 = score.score_variant("v2", runs, exp)
    assert list(res2["topics"]) == ["one"]
    cmp_md = score.render_comparison([res, res2])
    assert "| v1 |" in cmp_md and "| v2 |" in cmp_md


def test_cli_score_writes_scorecard(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    _make_run(runs / "v1" / "one", OK_LOG)
    monkeypatch.setattr("manimgen.bench.cli.RUNS_DIR", runs)
    assert main(["score", "v1"]) == 0
    assert (runs / "v1" / "scorecard.md").exists()
    assert json.loads((runs / "v1" / "scorecard.json").read_text())["variant"] == "v1"
    assert "| one |" in capsys.readouterr().out


def test_topics_file_is_valid():
    topics = load_topics()
    assert len(topics) == 10
    slugs = {t["slug"] for t in topics}
    assert {"pythag", "odds", "point9"} <= slugs
    assert {t["format"] for t in topics} == {
        "puzzle",
        "misconception",
        "proof",
        "worked",
    }
    with pytest.raises(ValueError):
        load_topics(only=["nope"])


def test_xstack_layout_and_command(tmp_path):
    assert sheet.xstack_layout(5) == "0_0|w0_0|w0+w0_0|w0+w0+w0_0|0_h0"
    assert sheet.tile_size(1080, 1920) == (270, 480)
    assert sheet.tile_size(1920, 1080) == (480, 270)
    cmd = sheet.build_command(
        [(tmp_path / "a.mp4", 10.0)], (270, 480), tmp_path / "o.png"
    )
    assert cmd.count("-i") == 4
    assert "0.500" in cmd and "9.500" in cmd


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="no ffmpeg"
)
def test_make_sheet_real_video(tmp_path):
    vid = tmp_path / "v.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=108x192:rate=10:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(vid),
        ],
        check=True,
    )
    out = sheet.make_sheet([vid, vid], tmp_path / "sheet.png")
    assert out.exists() and out.stat().st_size > 0
    _, w, h = sheet.probe_video(vid)
    assert (w, h) == (108, 192)
    from PIL import Image

    with Image.open(out) as im:
        assert im.size == (270 * 4, 480 * 2)
