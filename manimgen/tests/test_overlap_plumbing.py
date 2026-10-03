"""Wiring of the render-side overlap probe into the repair path (#97).

The probe's findings travel: manimgl child -> report file -> RenderResult
(and a sidecar next to the video) -> validate_render / retry_scene as hard
``OVERLAP:`` defects -> the bounded visual fix -> ACCEPTED_WITH_DEFECTS when
retries cannot remove them. No real manimgl and no LLM: every seam is faked.
"""

from __future__ import annotations

import importlib
import json
import logging
import os

import pytest

from manimgen import cli, paths, procutil
from manimgen.probes import overlap_report
from manimgen.types import GateResult, SectionStatus
from manimgen.validator import render_command
from manimgen.validator import retry as retry_module
from manimgen.validator import runner as runner_mod

pytestmark = pytest.mark.integration

_SCENE = "from manimlib import *\n\n\nclass S(Scene):\n    pass\n"
_FINDING = {"time": 6.57, "a": "while lo <= hi:", "b": "90%", "fraction": 0.4}


def _clean_frames(*_a, **_k):
    return type("R", (), {"ok": True, "skipped": False, "issues": []})()


# ---------------------------------------------------------------------------
# run_manimgl: env, report, sidecar
# ---------------------------------------------------------------------------


@pytest.fixture
def scene(tmp_path):
    p = tmp_path / "section_01.py"
    p.write_text(_SCENE, encoding="utf-8")
    return str(p)


def _fake_child(monkeypatch, write, video):
    """Fake run_tree: optionally writes a report where the env says."""
    seen = {}

    def fake(cmd, **kw):
        seen["env"] = kw["env"]
        path = kw["env"].get(overlap_report.REPORT_ENV)
        if path and write is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(write)
        return (0, "", "", False)

    monkeypatch.setattr(procutil, "run_tree", fake)
    monkeypatch.setattr(runner_mod, "_find_rendered_video", lambda *a, **k: video)
    return seen


def test_run_manimgl_returns_probe_overlaps_and_saves_sidecar(
    scene, tmp_path, monkeypatch
):
    video = str(tmp_path / "S.mp4")
    report = json.dumps({"findings": [_FINDING], "probe_error": None})
    seen = _fake_child(monkeypatch, report, video)

    res = render_command.run_manimgl(scene, "S", timeout=5)

    assert res.ok
    assert res.overlaps == (
        overlap_report.Overlap(6.57, "while lo <= hi:", "90%", 0.4),
    )
    assert res.probe_error is None
    env = seen["env"]
    assert env["PYTHONPATH"].split(os.pathsep)[0] == overlap_report.bootstrap_dir()
    # The report lived in a private temp dir that is gone after the render.
    assert not os.path.exists(env[overlap_report.REPORT_ENV])
    assert overlap_report.load_for_video(video).overlaps == res.overlaps


def test_run_manimgl_probe_error_does_not_fail_the_render(scene, tmp_path, monkeypatch):
    video = str(tmp_path / "S.mp4")
    _fake_child(monkeypatch, "{broken json", video)
    res = render_command.run_manimgl(scene, "S", timeout=5)
    assert res.ok and res.video_path == video
    assert res.overlaps == ()
    assert res.probe_error


def test_run_manimgl_without_report_is_clean_and_clears_stale_sidecar(
    scene, tmp_path, monkeypatch
):
    video = str(tmp_path / "S.mp4")
    with open(overlap_report.sidecar_path(video), "w", encoding="utf-8") as f:
        json.dump({"findings": [_FINDING]}, f)
    _fake_child(monkeypatch, None, video)
    res = render_command.run_manimgl(scene, "S", timeout=5)
    assert res.ok and res.overlaps == () and res.probe_error is None
    assert not os.path.exists(overlap_report.sidecar_path(video))


def test_probe_switched_off_sets_no_env(scene, tmp_path, monkeypatch):
    monkeypatch.setenv(overlap_report.DISABLE_ENV, "0")
    video = str(tmp_path / "S.mp4")
    with open(overlap_report.sidecar_path(video), "w", encoding="utf-8") as f:
        json.dump({"findings": [_FINDING]}, f)
    seen = _fake_child(monkeypatch, None, video)
    res = render_command.run_manimgl(scene, "S", timeout=5)
    assert res.ok and res.overlaps == ()
    # A stale report from an earlier probed render must not be re-read.
    assert not os.path.exists(overlap_report.sidecar_path(video))
    assert overlap_report.REPORT_ENV not in seen["env"]
    assert overlap_report.bootstrap_dir() not in seen["env"].get("PYTHONPATH", "")


def test_render_env_allowlist_keeps_pythonpath(monkeypatch):
    from manimgen.validator.env import get_render_env

    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    assert get_render_env().get("PYTHONPATH") == "/somewhere"


# ---------------------------------------------------------------------------
# validate_render: overlaps are hard
# ---------------------------------------------------------------------------


@pytest.fixture
def video(tmp_path, monkeypatch):
    import manimgen.validator.render_validator as rv

    path = tmp_path / "v.mp4"
    path.write_bytes(b"\x00")
    monkeypatch.setattr(rv, "check_frames", _clean_frames)
    return str(path)


def test_validate_render_overlap_is_hard(video):
    from manimgen.validator.render_validator import validate_render

    ov = (overlap_report.Overlap(6.57, "while lo <= hi:", "90%", 0.4),)
    vr = validate_render(video, "", "/s.py", None, overlaps=ov)
    assert vr.severity == "hard" and not vr.ok
    assert vr.issues[0].startswith("OVERLAP:")
    assert "90%" in vr.issues[0] and "t=6.6s" in vr.issues[0]


def test_validate_render_reads_the_sidecar(video):
    from manimgen.validator.render_validator import validate_render

    with open(overlap_report.sidecar_path(video), "w", encoding="utf-8") as f:
        json.dump({"findings": [_FINDING]}, f)
    vr = validate_render(video, "", "/s.py", None)
    assert vr.severity == "hard"


def test_validate_render_without_overlaps_is_unchanged(video):
    from manimgen.validator.render_validator import validate_render

    assert validate_render(video, "", "/s.py", None, overlaps=()).severity == "none"
    assert validate_render(video, "", "/s.py", None).severity == "none"


def test_validate_render_probe_error_changes_nothing(video):
    from manimgen.validator.render_validator import validate_render

    with open(overlap_report.sidecar_path(video), "w", encoding="utf-8") as f:
        json.dump({"findings": [], "probe_error": "observe: RuntimeError"}, f)
    assert validate_render(video, "", "/s.py", None).severity == "none"


# ---------------------------------------------------------------------------
# retry_scene: the bounded visual fix
# ---------------------------------------------------------------------------


@pytest.fixture
def retry_mod():
    importlib.reload(retry_module)
    yield retry_module
    importlib.reload(retry_module)


def _stub(retry_mod, monkeypatch, tmp_path, captures):
    scene = tmp_path / "s.py"
    scene.write_text(_SCENE, encoding="utf-8")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    chat_calls: list = []
    layout_calls: list = []
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr("manimgen.validator.frame_checker.check_frames", _clean_frames)
    monkeypatch.setattr(
        retry_mod,
        "check_layout",
        lambda v: (
            layout_calls.append(v)
            or {"ok": True, "issues": "", "frames": [], "skipped": False}
        ),
    )
    it = iter(captures)

    def _capture(p, c):
        overlaps = next(it, captures[-1])
        return {
            "success": True,
            "video_path": str(video),
            "stderr": "",
            "overlaps": overlaps,
        }

    monkeypatch.setattr(retry_mod, "_run_and_capture", _capture)
    monkeypatch.setattr(
        retry_mod,
        "chat",
        lambda **kw: chat_calls.append(kw) or _SCENE + "# moved\n",
    )
    return str(scene), str(video), chat_calls, layout_calls


_OV = (overlap_report.Overlap(6.57, "while lo <= hi:", "90%", 0.4),)


def test_overlap_enters_the_visual_fix_once_and_is_repaired(
    retry_mod, monkeypatch, tmp_path
):
    retry_mod.reset_run_budget()
    scene, video, chat_calls, layout_calls = _stub(
        retry_mod, monkeypatch, tmp_path, [_OV, ()]
    )
    ok, path = retry_mod.retry_scene({"id": "s"}, _SCENE, "S", scene)
    assert ok and path == video
    assert len(chat_calls) == 1
    assert chat_calls[0]["role"] == "visual_fix"
    assert "OVERLAP:" in chat_calls[0]["user"]
    assert "90%" in chat_calls[0]["user"]
    # The vision checker is not paid for while a zero-cost defect is known.
    assert len(layout_calls) == 1  # only on the clean second render


def test_unfixable_overlap_is_bounded(retry_mod, monkeypatch, tmp_path):
    monkeypatch.setenv("MANIMGEN_MAX_VISUAL_LLM_CALLS", "99")
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "999")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()
    scene, video, chat_calls, _ = _stub(retry_mod, monkeypatch, tmp_path, [_OV])
    ok, path = retry_mod.retry_scene({"id": "s"}, _SCENE, "S", scene)
    assert ok and path == video  # ships the best render, as for other defects
    assert len(chat_calls) == 1  # same signature: no second paid call


def test_overlap_respects_the_visual_budget(retry_mod, monkeypatch, tmp_path):
    monkeypatch.setenv("MANIMGEN_MAX_VISUAL_LLM_CALLS", "0")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()
    scene, _, chat_calls, _ = _stub(retry_mod, monkeypatch, tmp_path, [_OV])
    ok, _ = retry_mod.retry_scene({"id": "s"}, _SCENE, "S", scene)
    assert ok and chat_calls == []


def test_no_overlap_changes_nothing(retry_mod, monkeypatch, tmp_path):
    retry_mod.reset_run_budget()
    scene, video, chat_calls, layout_calls = _stub(
        retry_mod, monkeypatch, tmp_path, [()]
    )
    assert retry_mod.retry_scene({"id": "s"}, _SCENE, "S", scene) == (True, video)
    assert chat_calls == [] and len(layout_calls) == 1


# ---------------------------------------------------------------------------
# cli._render_with_retry: first pass forces retry, leftovers are reported
# ---------------------------------------------------------------------------


def _gate():
    return GateResult(
        code="CODE", class_name="S", scene_path="/tmp/s.py", timing_blocked=False
    )


def test_first_pass_overlap_forces_retry_and_is_reported(monkeypatch, tmp_path):
    video = str(tmp_path / "S.mp4")
    with open(video, "wb") as f:
        f.write(b"\x00")
    with open(overlap_report.sidecar_path(video), "w", encoding="utf-8") as f:
        json.dump({"findings": [_FINDING]}, f)
    import manimgen.validator.render_validator as rv

    monkeypatch.setattr(rv, "check_frames", _clean_frames)
    monkeypatch.setattr(cli, "run_scene", lambda p, c: (True, video))
    calls = []
    monkeypatch.setattr(
        cli, "retry_scene", lambda *a, **k: calls.append(1) or (True, video)
    )
    monkeypatch.setattr(cli, "_scene_file_blocking_freezes", lambda p, d: [])

    result = cli._render_with_retry({}, _gate(), None, logging.getLogger("t"))

    assert calls == [1]
    assert result.status == SectionStatus.ACCEPTED_WITH_DEFECTS
    assert "text overlap" in result.reason and "90%" in result.reason


def test_first_pass_without_overlap_ships(monkeypatch, tmp_path):
    video = str(tmp_path / "S.mp4")
    with open(video, "wb") as f:
        f.write(b"\x00")
    import manimgen.validator.render_validator as rv

    monkeypatch.setattr(rv, "check_frames", _clean_frames)
    monkeypatch.setattr(cli, "run_scene", lambda p, c: (True, video))
    monkeypatch.setattr(
        cli, "retry_scene", lambda *a, **k: pytest.fail("retry must not run")
    )
    result = cli._render_with_retry({}, _gate(), None, logging.getLogger("t"))
    assert result.status == SectionStatus.OK and result.reason == "first render"
