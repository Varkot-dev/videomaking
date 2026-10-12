"""The scene safety gate is a HARD block on every path that executes scene code.

Paths that hand a scene file to manimgl (#87):
  - runner.run_scene          (first-pass render)        -> run_manimgl
  - retry._run_and_capture    (every retry attempt)      -> run_manimgl
  - fallback.fallback_scene   (title-card fallback)      -> run_manimgl
and run_manimgl itself refuses a rejected file. The generator routes a rejected
draft away from the first render, and retry discards a rejected LLM fix.

The process leaf (procutil.run_tree) and the LLM are faked; no scene is ever
executed. Unsafe scenes are inert strings written to temp files.
"""

from __future__ import annotations

import pytest

from manimgen import paths, procutil
from manimgen.validator import fallback as fallback_mod
from manimgen.validator import render_command
from manimgen.validator import retry as retry_mod
from manimgen.validator import runner as runner_mod
from manimgen.validator.scene_ast_gate import inspect_scene_code

pytestmark = pytest.mark.security

SAFE = (
    "from manimlib import *\n\n\n"
    "class S(Scene):\n"
    "    def construct(self):\n"
    "        self.wait(1)\n"
)
UNSAFE = (
    "from manimlib import *\n\n\n"
    "class S(Scene):\n"
    "    def construct(self):\n"
    "        import subprocess\n"
    "        subprocess.run(['id'])\n"
)
_OK_PRECHECK = {"ok": True, "applied_fixes": [], "layout_warnings": [], "stderr": ""}


@pytest.fixture
def leaf(monkeypatch, tmp_path):
    """Fake process leaf that records every command it is asked to run."""
    calls: list = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return (0, "", "", False)

    monkeypatch.setattr(procutil, "run_tree", fake)
    monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(paths, "scenes_dir", lambda: str(tmp_path))
    monkeypatch.setattr(runner_mod, "_find_rendered_video", lambda *a, **k: None)
    for mod in (runner_mod, retry_mod):
        monkeypatch.setattr(mod, "precheck_and_autofix_file", lambda p: _OK_PRECHECK)
    return calls


def _write(tmp_path, code: str, name: str = "section_01.py") -> str:
    p = tmp_path / name
    p.write_text(code, encoding="utf-8")
    return str(p)


def test_run_manimgl_refuses_a_rejected_file(tmp_path, leaf):
    res = render_command.run_manimgl(_write(tmp_path, UNSAFE), "S")
    assert leaf == [], "manimgl was started on a rejected scene"
    assert not res.ok and res.video_path is None and not res.timed_out
    assert "SceneSafetyGateError" in res.stderr
    assert "subprocess" in res.stderr and "line 6" in res.stderr


def test_run_manimgl_fails_closed_on_a_missing_file(tmp_path, leaf):
    res = render_command.run_manimgl(str(tmp_path / "nope.py"), "S")
    assert leaf == []
    assert not res.ok


def test_run_manimgl_still_renders_a_safe_file(tmp_path, leaf):
    render_command.run_manimgl(_write(tmp_path, SAFE), "S")
    assert len(leaf) == 1


def test_runner_hard_blocks_and_logs_findings(tmp_path, leaf):
    ok, video = runner_mod.run_scene(_write(tmp_path, UNSAFE), "S")
    assert (ok, video) == (False, None)
    assert leaf == []
    logs = list((tmp_path / "logs").glob("S_*.log"))
    assert logs and "SceneSafetyGateError" in logs[0].read_text(encoding="utf-8")


def test_retry_attempt_hard_blocks_and_returns_findings(tmp_path, leaf):
    result = retry_mod._run_and_capture(_write(tmp_path, UNSAFE), "S")
    assert result["success"] is False
    assert "SceneSafetyGateError" in result["stderr"]
    assert leaf == []


def test_rejected_llm_error_fix_is_discarded_not_rendered(
    tmp_path, leaf, monkeypatch
):
    """An LLM fix that fails the gate is reverted and never reaches manimgl."""
    monkeypatch.setattr(retry_mod, "MAX_RETRIES", 3)
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(retry_mod, "apply_error_aware_fixes", lambda c, e: (c, []))
    retry_mod.reset_run_budget()
    rendered: list[str] = []

    def fake_run(scene_path, class_name):
        with open(scene_path, encoding="utf-8") as f:
            rendered.append(f.read())
        return {"success": False, "video_path": None, "stderr": "TypeError: boom"}

    monkeypatch.setattr(retry_mod, "_run_and_capture", fake_run)
    monkeypatch.setattr(retry_mod, "chat", lambda **kw: UNSAFE)
    scene = _write(tmp_path, SAFE)

    ok, video = retry_mod.retry_scene({"id": "section_01"}, SAFE, "S", scene)

    assert (ok, video) == (False, None)
    assert rendered == [SAFE], "the rejected fix was rendered"
    with open(scene, encoding="utf-8") as f:
        assert f.read() == SAFE, "the rejected fix was left on disk"


def test_rejected_llm_visual_fix_is_discarded_and_best_render_kept(
    tmp_path, leaf, monkeypatch
):
    from manimgen.validator.frame_checker import FrameCheckResult

    monkeypatch.setattr(retry_mod, "MAX_RETRIES", 3)
    monkeypatch.setattr(retry_mod, "MAX_VISUAL_LLM_FIX_CALLS", 2)
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    retry_mod.reset_run_budget()
    renders = {"n": 0}

    def fake_run(scene_path, class_name):
        renders["n"] += 1
        return {"success": True, "video_path": "/fake/v.mp4", "stderr": ""}

    monkeypatch.setattr(retry_mod, "_run_and_capture", fake_run)
    monkeypatch.setattr(
        "manimgen.validator.frame_checker.check_frames",
        lambda p: FrameCheckResult(ok=True),
    )
    monkeypatch.setattr(
        retry_mod,
        "check_layout",
        lambda p: {"ok": False, "issues": "ISSUE: overlap", "skipped": False},
    )
    monkeypatch.setattr(retry_mod, "_request_visual_fix", lambda *a, **k: UNSAFE)
    scene = _write(tmp_path, SAFE)

    ok, video = retry_mod.retry_scene({"id": "section_01"}, SAFE, "S", scene)

    assert (ok, video) == (True, "/fake/v.mp4")
    assert renders["n"] == 1, "the rejected visual fix was rendered"
    with open(scene, encoding="utf-8") as f:
        assert f.read() == SAFE


def test_generator_routes_a_rejected_draft_away_from_the_first_render(
    tmp_path, monkeypatch
):
    from manimgen.generator import scene_generator

    monkeypatch.setattr(scene_generator, "chat", lambda **kw: UNSAFE)
    monkeypatch.setattr(scene_generator, "load_reference_frames", lambda: [])
    monkeypatch.setattr(scene_generator, "precheck_and_autofix", lambda c: c)
    monkeypatch.setattr(
        scene_generator, "precheck_and_autofix_file", lambda p: _OK_PRECHECK
    )
    monkeypatch.setattr(scene_generator.paths, "scenes_dir", lambda: str(tmp_path))
    section = {"id": "section_01", "title": "T", "narration": "Hello.", "cues": []}

    with pytest.raises(scene_generator.ScenePrecheckError) as info:
        scene_generator.generate_scenes(section, cue_durations=[3.0])
    assert "scene safety gate" in str(info.value)
    assert "subprocess" in str(info.value)


def test_fallback_scene_passes_the_gate_and_neutralises_plan_text(
    tmp_path, leaf, monkeypatch
):
    written: list[str] = []

    def fake_render(scene_path, class_name, timeout=None):
        with open(scene_path, encoding="utf-8") as f:
            written.append(f.read())
        return render_command.RenderResult(False, None, "", "x", 1, False)

    monkeypatch.setattr(fallback_mod, "run_manimgl", fake_render)
    section = {
        "id": "section_02",
        "title": "See https://example.com/a.svg",
        "narration": "",
        # A string here used to be formatted into the source unquoted.
        "duration_seconds": "1)\n        import os  #",
    }
    fallback_mod.fallback_scene(section)

    assert written and inspect_scene_code(written[0]).ok, written
    assert "https://" not in written[0]
    assert "import os" not in written[0]


def test_plain_fallback_scene_passes_the_gate(tmp_path, leaf, monkeypatch):
    written: list[str] = []

    def fake_render(scene_path, class_name, timeout=None):
        with open(scene_path, encoding="utf-8") as f:
            written.append(f.read())
        return render_command.RenderResult(False, None, "", "x", 1, False)

    monkeypatch.setattr(fallback_mod, "run_manimgl", fake_render)
    fallback_mod.fallback_scene(
        {"id": "section_03", "title": "Binary search", "narration": "We halve it."}
    )
    assert written and inspect_scene_code(written[0]).ok
    assert "Binary search" in written[0]
