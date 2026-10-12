"""Per-section failure isolation and the usage-limit stop (#72).

cli.main() runs through the real section pipeline with only the leaf seams of
the #66 harness faked (planner and Director LLM, edge-tts, manimgl, ffmpeg).

- An ordinary error in one section (a Director call that fails, a cut that
  fails) must not end the run: the other sections are assembled into a
  partial video, the failure is reported, and the exit code is 3.
- A usage-limit or plan-allowance stop (PaidApiBlockedError, or the claude -p
  "usage limit" failure) must stop the run cleanly with no traceback: no
  later section starts, nothing is assembled, the manifest says what
  happened, the user is told to run `manimgen --resume` after the reset, and
  the exit code is 4.
"""

import json
import os
import sys

import pytest

import manimgen.renderer.muxer as muxer
import manimgen.validator.retry as retry
from manimgen import cli
from manimgen.llm import PaidApiBlockedError
from tests.test_cross_plan_cache import _Fakes, _plan

THREE = _plan(
    "failures",
    ["alpha words here", "beta words here", "gamma words here"],
    [[0], [0], [0]],
)
LIMIT_TEXT = (
    "claude -p failed (not retryable): Claude AI usage limit reached|1760000000"
)


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    f = _Fakes(tmp_path, monkeypatch)
    retry.reset_run_budget()
    return f


def _main(fakes, monkeypatch, plan, *argv) -> int:
    if plan is not None:
        fakes.plan = plan
    monkeypatch.setattr(sys, "argv", ["manimgen", *argv])
    fakes.assembled = []
    try:
        cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    return 0


def _manifest(fakes) -> dict:
    with open(
        os.path.join(fakes.dirs["videos"], "run_manifest.json"), encoding="utf-8"
    ) as f:
        return json.load(f)


def _director_raises_for(fakes, monkeypatch, section_id, exc):
    real = fakes._generate_scenes
    started = []

    def gen(section, cue_durations=None, overview=None):
        started.append(section["id"])
        if section["id"] == section_id:
            raise exc
        return real(section, cue_durations, overview)

    monkeypatch.setattr(cli, "generate_scenes", gen)
    return started


class TestPartialVideo:
    def test_generic_error_assembles_the_rest_and_exits_3(
        self, fakes, monkeypatch, capsys
    ):
        _director_raises_for(
            fakes, monkeypatch, "section_02", RuntimeError("claude -p failed: 500")
        )
        rc = _main(fakes, monkeypatch, THREE, "failures")
        assert rc == 3
        # Sections 1 and 3 shipped, in order.
        assert len(fakes.assembled) == 2
        assert "section_01" in fakes.assembled[0]
        assert "section_03" in fakes.assembled[1]
        m = _manifest(fakes)
        assert [s["status"] for s in m["sections"]] == ["ok", "errored", "ok"]
        assert "claude -p failed: 500" in m["sections"][1]["reason"]
        assert m["result"] == "degraded"
        assert "errored" in capsys.readouterr().out

    def test_failing_cut_drops_only_that_section(self, fakes, monkeypatch):
        real_cut = fakes._cut

        def cut(video_path, start, dur, out_path, i):
            if "section_02" in out_path:
                raise RuntimeError("ffmpeg cut failed")
            return real_cut(video_path, start, dur, out_path, i)

        monkeypatch.setattr(muxer, "_cut_one", cut)
        rc = _main(fakes, monkeypatch, THREE, "failures")
        assert rc == 3
        m = _manifest(fakes)
        assert [s["status"] for s in m["sections"]] == ["ok", "dropped", "ok"]
        assert "ffmpeg cut failed" in m["sections"][1]["reason"]
        assert len(fakes.assembled) == 2

    def test_every_section_errors_is_no_video(self, fakes, monkeypatch, capsys):
        monkeypatch.setattr(
            cli,
            "generate_scenes",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("bad plan")),
        )
        rc = _main(fakes, monkeypatch, THREE, "failures")
        assert rc == 1
        assert "No video was produced" in capsys.readouterr().err
        assert [s["status"] for s in _manifest(fakes)["sections"]] == ["errored"] * 3


class TestUsageLimitStop:
    @pytest.mark.parametrize(
        "exc",
        [
            PaidApiBlockedError("plan is at 95% of its five_hour allowance"),
            RuntimeError(LIMIT_TEXT),
        ],
        ids=["plan_guard", "claude_usage_limit"],
    )
    def test_stops_cleanly_with_exit_4(self, fakes, monkeypatch, capsys, exc):
        started = _director_raises_for(fakes, monkeypatch, "section_02", exc)
        rc = _main(fakes, monkeypatch, THREE, "failures")
        assert rc == 4
        assert started == ["section_01", "section_02"], "section 3 must not start"
        assert fakes.assembled == [], "a stopped run must not assemble"
        m = _manifest(fakes)
        assert m["result"] == "usage_limit"
        assert m["exit_code"] == 4
        assert m["output"] is None
        assert [s["status"] for s in m["sections"]] == ["ok", "errored", "not_run"]
        cap = capsys.readouterr()
        text = cap.out + cap.err
        assert "manimgen --resume" in text
        assert "Traceback" not in text

    def test_stop_names_the_reset_time_when_known(self, fakes, monkeypatch, capsys):
        import time

        from manimgen import llm

        resets = time.time() + 3600
        monkeypatch.setattr(llm, "_plan_windows", {"five_hour": (0.97, resets)})
        _director_raises_for(fakes, monkeypatch, "section_01", RuntimeError(LIMIT_TEXT))
        assert _main(fakes, monkeypatch, THREE, "failures") == 4
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(resets))
        assert f"around {when} local time" in capsys.readouterr().out
        assert _manifest(fakes)["next_steps"][0].count(when) == 1

    def test_resume_after_the_stop_reuses_finished_sections(self, fakes, monkeypatch):
        _director_raises_for(
            fakes, monkeypatch, "section_02", PaidApiBlockedError("limit")
        )
        assert _main(fakes, monkeypatch, THREE, "failures") == 4
        codegen = fakes.codegen_calls
        monkeypatch.setattr(cli, "generate_scenes", fakes._generate_scenes)
        assert _main(fakes, monkeypatch, None, "--resume") == 0
        assert fakes.codegen_calls == codegen + 2  # sections 2 and 3 only
        assert len(fakes.assembled) == 3

    def test_usage_limit_while_planning_exits_4(self, fakes, monkeypatch, capsys):
        def plan(topic):
            raise PaidApiBlockedError("plan is at 95% of its five_hour allowance")

        monkeypatch.setattr(cli, "plan_lesson", plan)
        rc = _main(fakes, monkeypatch, None, "failures")
        assert rc == 4
        err = capsys.readouterr()
        text = err.out + err.err
        assert "five_hour allowance" in text
        # No plan was cached, so --resume would not help: say to rerun instead.
        assert "manimgen --resume" not in text
        assert "rerun" in text.lower()

    def test_layout_checker_does_not_hide_a_usage_stop(self, monkeypatch, tmp_path):
        """check_layout used to report any chat() failure as 'skipped', so a
        usage limit there was hidden until the next Director call."""
        from manimgen.validator import layout_checker

        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        monkeypatch.setattr(layout_checker, "_sample_frames", lambda p: ["f"])
        monkeypatch.setattr(layout_checker, "load_reference_frames", lambda: [])
        monkeypatch.setattr(layout_checker, "_load_layout_system_prompt", lambda: "")

        def chat(**k):
            raise PaidApiBlockedError("limit")

        monkeypatch.setattr(layout_checker, "chat", chat)
        with pytest.raises(PaidApiBlockedError):
            layout_checker.check_layout(str(video))

        monkeypatch.setattr(
            layout_checker,
            "chat",
            lambda **k: (_ for _ in ()).throw(RuntimeError(LIMIT_TEXT)),
        )
        with pytest.raises(RuntimeError):
            layout_checker.check_layout(str(video))

        # Any other failure is still a skipped (unverified) check.
        monkeypatch.setattr(
            layout_checker,
            "chat",
            lambda **k: (_ for _ in ()).throw(RuntimeError("network blip")),
        )
        assert layout_checker.check_layout(str(video))["skipped"] is True
