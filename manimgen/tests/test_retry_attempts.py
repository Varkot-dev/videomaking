"""Render attempts are spent on renders, not on free fixes or pre-render rejections.

Replays what the 2026-10-03 gradient-descent run did. With 3 attempts, the first
one usually went to a scene that never rendered (the unknown-name check rejected
`Star`/`Dot3D` before manimgl ran) or to a crash a token-free local fix repairs
(NumberLine(length=)). The one visual fix that followed then had no attempt left
to render it, or to repair a crash it introduced, so the earlier defective
render shipped. In two of four defective sections the visual fix was never seen.
"""

from __future__ import annotations

import pytest

from manimgen import paths
from manimgen.probes.overlap_report import Overlap
from manimgen.validator import retry as retry_mod

_SCENE = "from manimlib import *\n\n\nclass S(Scene):\n    pass\n"
_KWARG_CRASH = "TypeError: Mobject.__init__() got an unexpected keyword argument 'length'"
_OVERLAP = Overlap(14.6, "0.4", "0.6", 0.36)


def _clean_frames(*_a, **_k):
    return type("R", (), {"ok": True, "skipped": False, "issues": []})()


def _rendered(video, overlaps=()):
    return {"success": True, "video_path": video, "stderr": "", "overlaps": overlaps}


def _crash(stderr):
    return {"success": False, "video_path": None, "stderr": stderr, "rendered": True}


def _rejected(stderr):
    return {"success": False, "video_path": None, "stderr": stderr, "rendered": False}


@pytest.fixture
def loop(monkeypatch, tmp_path):
    """Drive retry_scene through a scripted list of render outcomes."""
    scene = tmp_path / "s.py"
    scene.write_text(_SCENE, encoding="utf-8")
    calls = {"render": 0, "chat": []}

    monkeypatch.setattr(retry_mod, "MAX_RETRIES", 3)
    monkeypatch.setattr(retry_mod, "MAX_ERROR_LLM_FIX_CALLS", 3)
    monkeypatch.setattr(retry_mod, "MAX_VISUAL_LLM_FIX_CALLS", 3)
    retry_mod.reset_run_budget()
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr("manimgen.validator.frame_checker.check_frames", _clean_frames)
    monkeypatch.setattr(
        retry_mod,
        "check_layout",
        lambda v: {"ok": True, "issues": "", "frames": [], "skipped": False},
    )

    def _local_fix(code, stderr):
        if "unexpected keyword argument 'length'" in stderr:
            return code + "# length removed\n", ["removed unexpected kwarg 'length' (1)"]
        return code, []

    monkeypatch.setattr(retry_mod, "apply_error_aware_fixes", _local_fix)

    def _chat(**kw):
        calls["chat"].append(kw["role"])
        return _SCENE + f"# fix {len(calls['chat'])}\n"

    monkeypatch.setattr(retry_mod, "chat", _chat)

    def run(outcomes):
        it = iter(outcomes)

        def _capture(p, c):
            calls["render"] += 1
            return next(it)

        monkeypatch.setattr(retry_mod, "_run_and_capture", _capture)
        result = retry_mod.retry_scene({"id": "s"}, _SCENE, "S", str(scene))
        return result, calls

    return run


def test_section_4_local_fix_after_visual_fix_gets_rendered(loop, tmp_path):
    # Run 3, section 4: crash fixed locally, render with overlaps, visual fix
    # re-introduces the bad kwarg, local fix again, then (now) a clean render.
    defective, clean = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    (ok, video), calls = loop(
        [
            _crash(_KWARG_CRASH),
            _rendered(defective, (_OVERLAP,)),
            _crash(_KWARG_CRASH),
            _rendered(clean),
        ]
    )
    assert ok and video == clean
    assert retry_mod.accepted_issues(video) == []
    assert calls["chat"] == ["visual_fix"]


def test_section_1_rejection_before_render_does_not_use_an_attempt(loop, tmp_path):
    # Run 3, section 1: the draft was rejected before manimgl ran (unknown name
    # `Star`), then a render with a defect, a visual fix that crashed, and (now)
    # a repair of that crash that renders clean.
    defective, clean = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    (ok, video), calls = loop(
        [
            _rejected("Unknown name `Star` (line 18)"),
            _rendered(defective, (_OVERLAP,)),
            _crash("ValueError: could not broadcast input array"),
            _rendered(clean),
        ]
    )
    assert ok and video == clean
    assert retry_mod.accepted_issues(video) == []
    assert calls["chat"] == ["error_fix", "visual_fix", "error_fix"]


def test_free_steps_are_bounded(loop):
    # A crash the local fix "repairs" every time must not loop forever.
    endless = [_crash(_KWARG_CRASH)] * 50
    (ok, video), calls = loop(endless)
    assert (ok, video) == (False, None)
    assert calls["render"] <= 2 * retry_mod.MAX_RETRIES


def test_rejections_are_bounded_by_the_error_budget(loop):
    endless = [_rejected(f"Unknown name `Star{i}`") for i in range(50)]
    (ok, video), calls = loop(endless)
    assert (ok, video) == (False, None)
    assert calls["chat"].count("error_fix") <= retry_mod.MAX_ERROR_LLM_FIX_CALLS
    assert calls["render"] <= 2 * retry_mod.MAX_RETRIES


def test_run_and_capture_marks_rejections_as_not_rendered(monkeypatch, tmp_path):
    from manimgen.validator.render_command import RenderResult

    scene = tmp_path / "s.py"
    scene.write_text(_SCENE, encoding="utf-8")

    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": False, "stderr": "x"}
    )
    assert retry_mod._run_and_capture(str(scene), "S")["rendered"] is False

    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    gate_reject = RenderResult(False, None, "", "gate", None, False)
    monkeypatch.setattr(retry_mod, "run_manimgl", lambda p, c: gate_reject)
    assert retry_mod._run_and_capture(str(scene), "S")["rendered"] is False

    crash = RenderResult(False, None, "", "Traceback", 1, False)
    monkeypatch.setattr(retry_mod, "run_manimgl", lambda p, c: crash)
    assert retry_mod._run_and_capture(str(scene), "S")["rendered"] is True

    timeout = RenderResult(False, None, "", "TimeoutExpired", None, True)
    monkeypatch.setattr(retry_mod, "run_manimgl", lambda p, c: timeout)
    assert retry_mod._run_and_capture(str(scene), "S")["rendered"] is True


# --- safety-gate rejections get their own error type and guidance ----------

_GATE_NAME = (
    "SceneSafetyGateError: the scene was not rendered because it uses "
    "constructs that are not allowed in generated scenes. Remove every "
    "construct listed below.\n- line 113: name 'se' is not allowed"
)
_GATE_TOPLEVEL = (
    "SceneSafetyGateError: the scene was not rendered because it uses "
    "constructs that are not allowed in generated scenes. Remove every "
    "construct listed below.\n- line 166: disallowed top-level Expr"
)


def test_gate_rejection_for_denied_name_is_classified_as_safety_gate():
    assert retry_mod._classify_error(_GATE_NAME) == retry_mod.SceneErrorType.SAFETY_GATE


def test_gate_rejection_for_top_level_expr_is_classified_as_safety_gate():
    got = retry_mod._classify_error(_GATE_TOPLEVEL)
    assert got == retry_mod.SceneErrorType.SAFETY_GATE


def test_gate_rejection_wins_over_keywords_quoted_in_its_findings():
    stderr = _GATE_TOPLEVEL + "\n- line 9: name 'TypeError' is not allowed (SyntaxError)"
    assert retry_mod._classify_error(stderr) == retry_mod.SceneErrorType.SAFETY_GATE


def test_traceback_quoting_the_gate_marker_mid_line_is_not_a_gate_rejection():
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "scene.py", line 3, in construct\n'
        "    x = undefined  # SceneSafetyGateError: quoted in a comment\n"
        "NameError: name 'undefined' is not defined"
    )
    assert retry_mod._classify_error(stderr) == retry_mod.SceneErrorType.RUNTIME


@pytest.mark.parametrize(
    "stderr, expected",
    [
        ("AttributeError: 'X' has no attribute 'y'", "ATTRIBUTE"),
        ("TypeError: bad argument", "TYPE"),
        ("RuntimeError: boom", "RUNTIME"),
        ("ValueError: could not broadcast input array", "RUNTIME"),
        ("SyntaxError: invalid syntax", "SYNTAX"),
        ("ModuleNotFoundError: No module named 'x'", "IMPORT"),
    ],
)
def test_ordinary_tracebacks_classify_as_before(stderr, expected):
    assert retry_mod._classify_error(stderr) == retry_mod.SceneErrorType[expected]


def test_real_gate_error_text_is_classified_as_safety_gate():
    from manimgen.validator.scene_ast_gate import format_gate_error, inspect_scene_code

    code = (
        "from manimlib import *\n\n\nclass S(Scene):\n"
        "    def construct(self):\n        se = 1\n"
    )
    gate = inspect_scene_code(code)
    assert not gate.ok
    text = format_gate_error(gate)
    assert "name 'se' is not allowed" in text
    assert retry_mod._classify_error(text) == retry_mod.SceneErrorType.SAFETY_GATE


def test_gate_rejection_fix_prompt_uses_gate_guidance(monkeypatch, loop, tmp_path):
    prompts = []

    def _chat(**kw):
        prompts.append(kw["user"])
        return _SCENE + "# fixed\n"

    monkeypatch.setattr(retry_mod, "chat", _chat)
    clean = str(tmp_path / "c.mp4")
    (ok, _video), _calls = loop([_rejected(_GATE_NAME), _rendered(clean)])
    assert ok
    assert len(prompts) == 1
    prompt = prompts[0]
    assert f"Error type: {retry_mod.SceneErrorType.SAFETY_GATE}" in prompt
    assert "static safety check" in prompt
    assert "sin_t" in prompt and "great_circle" in prompt
    assert "Simplify the scene logic" not in prompt
