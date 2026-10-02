"""Run outcome reporting (#71): per-section status, summary, manifest, exit code.

These tests drive cli.main() through the real section pipeline with the leaf
seams faked by the #66 harness (planner and Director LLM, edge-tts, manimgl,
ffmpeg). Each test then reads run_manifest.json and the exit code, which is
what the owner (or a script) sees after an unattended run.

Exit codes: 0 every section is ok or accepted with defects, 3 a video was
produced but a section is a fallback card, dropped, silent or errored.
"""

import json
import os
import subprocess
import sys

import pytest

import manimgen.renderer.muxer as muxer
import manimgen.renderer.tts as tts
import manimgen.validator.retry as retry
import manimgen.validator.runner as runner
from manimgen import cli
from tests.test_cross_plan_cache import _Fakes, _plan, _read, _write

TWO = _plan(
    "outcomes",
    ["first section narration here", "second section narration here"],
    [[0], [0]],
)


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    f = _Fakes(tmp_path, monkeypatch)
    retry.reset_run_budget()
    return f


def _main(fakes, monkeypatch, plan, *argv) -> int:
    """Run cli.main() like the console script does and return the exit code."""
    if plan is not None:
        fakes.plan = plan
    monkeypatch.setattr(sys, "argv", ["manimgen", *argv])
    fakes.assembled = []
    try:
        rc = cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    return rc or 0


def _manifest(fakes) -> dict:
    with open(
        os.path.join(fakes.dirs["videos"], "run_manifest.json"), encoding="utf-8"
    ) as f:
        return json.load(f)


def _statuses(fakes) -> list[str]:
    return [s["status"] for s in _manifest(fakes)["sections"]]


def _fail_render_for(fakes, monkeypatch, class_name):
    """The Director render of one section fails, as do the real retry loop's
    manimgl renders and LLM fix; only the deterministic fallback card renders.

    retry, fallback and runner share the one subprocess module, so a single
    fake manimgl dispatches on the scene class in the command line.
    """
    real = fakes._run_scene

    def run_scene(scene_path, cls):
        if cls == class_name:
            return False, None
        return real(scene_path, cls)

    def manimgl(cmd, *a, **k):
        ok = any(str(part).endswith("FallbackScene") for part in cmd)
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "boom")

    def find(cls, newer_than=None):
        p = os.path.join(fakes.dirs["videos"], f"{cls}.mp4")
        _write(p, f"render<fallback {cls}>")
        return p

    monkeypatch.setattr(cli, "run_scene", run_scene)
    monkeypatch.setattr(retry, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(retry, "chat", lambda **k: "not python at all (")
    monkeypatch.setattr(subprocess, "run", manimgl)
    monkeypatch.setattr(runner, "_find_rendered_video", find)


class TestCleanRun:
    def test_clean_run_exits_zero_and_writes_manifest(self, fakes, monkeypatch, capsys):
        rc = _main(fakes, monkeypatch, TWO, "outcomes")
        assert rc == 0
        m = _manifest(fakes)
        assert m["exit_code"] == 0
        assert m["result"] == "ok"
        assert m["title"] == "outcomes"
        assert m["output"].endswith("final.mp4")
        assert [s["id"] for s in m["sections"]] == ["section_01", "section_02"]
        assert _statuses(fakes) == ["ok", "ok"]
        out = capsys.readouterr().out
        assert "Run summary" in out
        assert "run_manifest.json" in out

    def test_manifest_written_atomically_as_utf8(self, fakes, monkeypatch):
        plan = _plan("café → limits", ["naïve words here"], [[0]])
        assert _main(fakes, monkeypatch, plan, "cafe") == 0
        path = os.path.join(fakes.dirs["videos"], "run_manifest.json")
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["title"] == "café → limits"
        assert not os.path.exists(path + ".tmp")

    def test_resume_of_clean_run_is_ok_and_cached(self, fakes, monkeypatch):
        assert _main(fakes, monkeypatch, TWO, "outcomes") == 0
        assert _main(fakes, monkeypatch, None, "--resume") == 0
        m = _manifest(fakes)
        assert _statuses(fakes) == ["ok", "ok"]
        assert all("cached" in s["reason"] for s in m["sections"])


class TestDegradedRuns:
    def test_fallback_section_exits_3(self, fakes, monkeypatch, capsys):
        _fail_render_for(fakes, monkeypatch, "Section02Scene")
        rc = _main(fakes, monkeypatch, TWO, "outcomes")
        assert rc == 3
        assert _statuses(fakes) == ["ok", "fallback"]
        m = _manifest(fakes)
        assert m["exit_code"] == 3 and m["result"] == "degraded"
        # The video still exists, and the fallback card is in it.
        assert any("fallback" in _read(p) for p in fakes.assembled)
        out = capsys.readouterr().out
        assert "fallback" in out and "section_02" in out

    def test_cached_fallback_stays_fallback_on_resume(self, fakes, monkeypatch):
        _fail_render_for(fakes, monkeypatch, "Section02Scene")
        assert _main(fakes, monkeypatch, TWO, "outcomes") == 3
        # Everything works now, but section 2's cached clips are the title card.
        monkeypatch.setattr(cli, "run_scene", fakes._run_scene)
        assert _main(fakes, monkeypatch, None, "--resume") == 3
        assert _statuses(fakes) == ["ok", "fallback"]

    def test_dropped_section_exits_3(self, fakes, monkeypatch):
        real_mux = fakes._mux

        def mux(video, audio, out):
            if "section_02" in out:
                raise RuntimeError("ffmpeg mux exploded")
            return real_mux(video, audio, out)

        monkeypatch.setattr(muxer, "mux_audio_video", mux)
        rc = _main(fakes, monkeypatch, TWO, "outcomes")
        assert rc == 3
        assert _statuses(fakes) == ["ok", "dropped"]
        assert len(fakes.assembled) == 1  # only section 1 shipped

    def test_silent_section_exits_3(self, fakes, monkeypatch):
        real_tts = fakes._generate_narration

        def gen(text, output_path, voice=None):
            if "second" in text:
                raise OSError("CERTIFICATE_VERIFY_FAILED")
            return real_tts(text, output_path, voice)

        monkeypatch.setattr(tts, "generate_narration", gen)
        rc = _main(fakes, monkeypatch, TWO, "outcomes")
        assert rc == 3
        m = _manifest(fakes)
        assert _statuses(fakes) == ["ok", "silent"]
        assert "CERTIFICATE_VERIFY_FAILED" in m["sections"][1]["reason"]

    def test_accepted_with_defects_is_listed_but_exits_0(
        self, fakes, monkeypatch, capsys
    ):
        """retry_scene accepted a render whose final scene still has a
        blocking freeze-frame tail: reported, but not a degraded run."""
        real_run = fakes._run_scene

        def run_scene(scene_path, cls):
            if cls == "Section02Scene":
                return False, None
            return real_run(scene_path, cls)

        def accept(section, code, cls, path, cue_durations=None):
            # The final source still under-fills the cue by seconds.
            _write(path, "# frozen\nself.wait(0.1)\n")
            return real_run(path, cls)

        monkeypatch.setattr(cli, "run_scene", run_scene)
        monkeypatch.setattr(cli, "retry_scene", accept)
        # Section 2 narration is long enough for a blocking freeze tail.
        plan = _plan(
            "defects",
            ["first words", "one two three four five six seven eight nine ten"],
            [[0], [0]],
        )
        rc = _main(fakes, monkeypatch, plan, "defects")
        assert rc == 0
        assert _statuses(fakes) == ["ok", "accepted_with_defects"]
        m = _manifest(fakes)
        assert "frozen tail" in m["sections"][1]["reason"]
        assert m["result"] == "ok"
        assert "accepted_with_defects" in capsys.readouterr().out

    def test_no_video_still_writes_manifest(self, fakes, monkeypatch, capsys):
        monkeypatch.setattr(
            muxer, "mux_audio_video", lambda *a: (_ for _ in ()).throw(OSError("x"))
        )
        rc = _main(fakes, monkeypatch, TWO, "outcomes")
        assert rc == 1
        assert "No video was produced" in capsys.readouterr().err
        m = _manifest(fakes)
        assert m["output"] is None
        assert _statuses(fakes) == ["dropped", "dropped"]
