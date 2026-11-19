"""Preflight wiring: config switch, cli flow, retry loop and drafts (all mocked).

No manimgl, no LLM. The real dry runs are in test_preflight_render.py.
"""

from __future__ import annotations

import logging
import os

import pytest

from manimgen import cli, paths
from manimgen.generator import scene_drafts
from manimgen.probes.overlap_report import Overlap
from manimgen.types import GateResult, SectionStatus
from manimgen.utils import draft_class_name
from manimgen.validator import preflight as pf_mod
from manimgen.validator import render_command
from manimgen.validator import retry as retry_mod
from manimgen.validator.preflight import PreflightResult, kit_findings_from

_SCENE = "from manimlib import *\n\n\nclass S(Scene):\n    pass\n"
_OVERLAP = Overlap(2.0, "alpha", "beta", 0.5)
_CRASH = "Traceback\nAttributeError: 'Text' object has no attribute 'nope'\n"


def _clean(**kw):
    return PreflightResult(ran=True, returncode=0, seconds=2.0, **kw)


def _found():
    return _clean(overlaps=(_OVERLAP,))


def _crashed():
    return PreflightResult(crashed=True, returncode=1, stderr=_CRASH, seconds=2.0)


# ---------------------------------------------------------------- config


def test_preflight_is_on_by_default_and_the_env_var_turns_it_off(monkeypatch):
    monkeypatch.delenv("MANIMGEN_PREFLIGHT", raising=False)
    assert paths.preflight_enabled() is True
    for off in ("0", "false", "off"):
        monkeypatch.setenv("MANIMGEN_PREFLIGHT", off)
        assert paths.preflight_enabled() is False
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "1")
    assert paths.preflight_enabled() is True


def test_config_switch_is_used_without_the_env_var(monkeypatch):
    monkeypatch.delenv("MANIMGEN_PREFLIGHT", raising=False)
    monkeypatch.setitem(paths._RENDERING, "preflight", False)
    assert paths.preflight_enabled() is False


def test_preflight_timeout_comes_from_config(monkeypatch):
    monkeypatch.setitem(paths._RENDERING, "preflight_timeout", 45)
    assert paths.preflight_timeout() == 45.0


# ------------------------------------------------------------ the result


def test_kit_lines_are_read_from_stdout_without_duplicates():
    out = (
        "x\n[scenekit] label 'a' overlaps\n  [scenekit] label 'a' overlaps\nKIT-AUDIT\n"
    )
    assert kit_findings_from(out) == ("label 'a' overlaps",)


def test_result_flags():
    assert _clean().clean and not _clean().actionable
    assert _found().actionable and not _found().clean
    assert _crashed().actionable and not _crashed().clean
    unknown = PreflightResult(timed_out=True)
    assert not unknown.clean and not unknown.actionable
    kit = _clean(kit_findings=("label 'x' has no free spot",))
    assert kit.actionable
    assert kit.placement_issues[0].startswith("LAYOUT KIT:")


def test_preflight_never_raises_and_classifies_a_crash(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(pf_mod, "run_manimgl", boom)
    res = pf_mod.preflight("x.py", "S")
    assert not res.ran and not res.crashed and not res.actionable

    monkeypatch.setattr(
        pf_mod,
        "run_manimgl",
        lambda *a, **k: render_command.RenderResult(False, None, "", _CRASH, 1, False),
    )
    res = pf_mod.preflight("x.py", "S")
    assert res.crashed and res.error_type == retry_mod.SceneErrorType.ATTRIBUTE


def test_dry_run_command_uses_skip_flag_and_a_temp_folder():
    cmd = render_command.build_manimgl_command("s.py", "S", dry_run_dir="/tmp/x")
    assert "-s" in cmd and cmd[cmd.index("--video_dir") + 1] == "/tmp/x"
    assert "-s" not in render_command.build_manimgl_command("s.py", "S")


# ------------------------------------------------------------------- cli


@pytest.fixture
def cli_flow(monkeypatch):
    calls = {"pf": 0, "scene": 0, "retry": [], "order": []}

    def fake_pf(gate, log):
        calls["pf"] += 1
        calls["order"].append("preflight")
        return calls["result"]

    def fake_scene(path, cls):
        calls["scene"] += 1
        calls["order"].append("render")
        return True, "/tmp/v.mp4"

    def fake_retry(*a, **k):
        calls["retry"].append(k)
        calls["order"].append("retry")
        return True, "/tmp/r.mp4"

    monkeypatch.setattr(cli, "run_scene", fake_scene)
    monkeypatch.setattr(cli, "retry_scene", fake_retry)
    monkeypatch.setattr(cli, "_preflight_gate", fake_pf)
    monkeypatch.setattr(cli, "accepted_issues", lambda v: [])
    import manimgen.validator.render_validator as rv

    monkeypatch.setattr(
        rv,
        "validate_render",
        lambda *a, **k: rv.ValidationResult(ok=True, severity="none", issues=[]),
    )
    calls["result"] = _clean()
    return calls


def _gate():
    return GateResult(
        code="C", class_name="S", scene_path="/tmp/s.py", timing_blocked=False
    )


def test_clean_preflight_leads_to_exactly_one_full_render(cli_flow):
    res = cli._render_with_retry({}, _gate(), None, logging.getLogger("t"))
    assert cli_flow["order"] == ["preflight", "render"]
    assert cli_flow["retry"] == []
    assert res.status == SectionStatus.OK


@pytest.mark.parametrize("found", [_found, _crashed])
def test_findings_skip_the_first_render_and_go_to_retry(cli_flow, found):
    cli_flow["result"] = found()
    res = cli._render_with_retry({}, _gate(), None, logging.getLogger("t"))
    assert cli_flow["order"] == ["preflight", "retry"]
    assert cli_flow["scene"] == 0
    assert cli_flow["retry"][0]["initial_preflight"] is cli_flow["result"]
    assert "first_render" not in cli_flow["retry"][0]
    assert res.ok


def test_an_unusable_preflight_does_not_block_the_render(cli_flow):
    cli_flow["result"] = PreflightResult(timed_out=True)
    cli._render_with_retry({}, _gate(), None, logging.getLogger("t"))
    assert cli_flow["order"] == ["preflight", "render"]


def test_preflight_off_is_the_old_flow(monkeypatch):
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "0")
    ran = []
    monkeypatch.setattr(pf_mod, "preflight", lambda *a, **k: ran.append(1))
    assert cli._preflight_gate(_gate(), logging.getLogger("t")) is None
    assert not ran


def test_blocked_gates_never_run_preflight(cli_flow):
    gate = GateResult(
        code="C",
        class_name="S",
        scene_path="/tmp/s.py",
        timing_blocked=False,
        precheck_blocked=True,
    )
    cli._render_with_retry({}, gate, None, logging.getLogger("t"))
    assert cli_flow["order"] == ["retry"]


# ----------------------------------------------------------- retry loop


@pytest.fixture
def loop(monkeypatch, tmp_path):
    """retry_scene with scripted dry runs and real renders."""
    scene = tmp_path / "s.py"
    scene.write_text(_SCENE, encoding="utf-8")
    calls = {"dry": [], "real": 0, "chat": [], "order": []}

    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "1")
    monkeypatch.setattr(retry_mod, "MAX_RETRIES", 3)
    monkeypatch.setattr(retry_mod, "MAX_ERROR_LLM_FIX_CALLS", 3)
    monkeypatch.setattr(retry_mod, "MAX_VISUAL_LLM_FIX_CALLS", 3)
    retry_mod.reset_run_budget()
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(
        "manimgen.validator.frame_checker.check_frames",
        lambda *a, **k: type("R", (), {"ok": True, "skipped": False, "issues": []})(),
    )
    monkeypatch.setattr(
        retry_mod,
        "check_layout",
        lambda v: {"ok": True, "issues": "", "frames": [], "skipped": False},
    )
    monkeypatch.setattr(retry_mod, "apply_error_aware_fixes", lambda c, e: (c, []))

    def chat(**kw):
        calls["chat"].append(kw["role"])
        calls["order"].append("fix")
        return _SCENE + f"# fix {len(calls['chat'])}\n"

    monkeypatch.setattr(retry_mod, "chat", chat)

    def run(dry_results, real=None, **kwargs):
        dry = list(dry_results)

        def fake_dry(path, cls):
            calls["order"].append("dry")
            calls["dry"].append(path)
            return dry.pop(0)

        def fake_real(path, cls, *a, **k):
            calls["real"] += 1
            calls["order"].append("real")
            return real or render_command.RenderResult(
                True, str(tmp_path / "v.mp4"), "", "", 0, False
            )

        monkeypatch.setattr(retry_mod, "run_preflight", fake_dry)
        monkeypatch.setattr(retry_mod, "run_manimgl", fake_real)
        return retry_mod.retry_scene({}, _SCENE, "S", str(scene), **kwargs)

    calls["run"] = run
    return calls


def test_defects_are_fixed_and_verified_by_preflight_then_one_real_render(loop):
    ok, video = loop["run"]([_clean()], initial_preflight=_found())
    assert ok and video
    assert loop["order"] == ["fix", "dry", "real"]
    assert len(loop["chat"]) == 1
    assert loop["real"] == 1


def test_defects_that_persist_use_dry_runs_until_the_budget_is_spent(loop):
    ok, video = loop["run"]([_found(), _found(), _found()], initial_preflight=_found())
    assert ok
    # Fix, dry (the same defects came back: signature repeats), then the real
    # render: only one real render in total, and the model was asked once.
    assert loop["real"] == 1
    assert loop["order"][-1] == "real"
    assert loop["order"].count("real") == 1
    assert loop["order"].count("fix") <= 2


def test_a_crash_found_by_preflight_takes_the_error_path(loop):
    ok, video = loop["run"]([_clean()], initial_preflight=_crashed())
    assert ok
    assert loop["order"] == ["fix", "dry", "real"]
    assert loop["real"] == 1


def test_a_clean_preflight_in_retry_goes_straight_to_the_real_render(loop):
    ok, video = loop["run"]([_clean()])
    assert ok
    assert loop["order"] == ["dry", "real"]
    assert loop["chat"] == []


def test_a_timed_out_preflight_does_not_stop_the_real_render(loop):
    ok, video = loop["run"]([PreflightResult(timed_out=True)])
    assert ok
    assert loop["order"] == ["dry", "real"]


def test_a_handed_over_render_is_checked_without_preflight(loop, tmp_path):
    first = tmp_path / "first.mp4"
    first.write_bytes(b"x")
    ok, video = loop["run"]([], first_render=str(first))
    assert ok
    assert loop["dry"] == []


def test_preflight_off_renders_every_attempt(loop, monkeypatch):
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "0")
    ok, video = loop["run"]([])
    assert ok and loop["dry"] == [] and loop["real"] == 1


def test_real_render_after_preflight_still_gets_the_normal_checks(loop):
    """A real render with a probe finding the dry run missed is fixed as before."""
    real = render_command.RenderResult(
        True, "/tmp/v.mp4", "", "", 0, False, overlaps=(_OVERLAP,)
    )
    ok, video = loop["run"]([_clean()], real=real)
    assert ok
    assert loop["order"][:2] == ["dry", "real"]
    assert "fix" in loop["order"]


# ---------------------------------------------------------------- drafts

SECTION = {"id": "section_01", "title": "T"}


def _draft(variant, tmp_path, code=None):
    cls = draft_class_name(SECTION, variant)
    path = tmp_path / f"section_01_draft{variant}.py"
    code = code or _SCENE.replace("class S", f"class {cls}")
    path.write_text(code, encoding="utf-8")
    d = scene_drafts.Draft(variant)
    d.gate = GateResult(
        code=code, class_name=cls, scene_path=str(path), timing_blocked=False
    )
    d.code_chars = len(code)
    return d


def test_drafts_are_scored_by_preflight_without_any_render(monkeypatch, tmp_path):
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "1")
    results = {
        "A": _clean(overlaps=(_OVERLAP,), kit_findings=("label has no spot",)),
        "B": _clean(),
        "C": _crashed(),
    }
    monkeypatch.setattr(
        "manimgen.validator.preflight.preflight",
        lambda path, cls: results[cls.split("Draft")[1][0]],
    )
    monkeypatch.setattr(
        render_command,
        "run_manimgl",
        lambda *a, **k: pytest.fail("a draft was rendered"),
    )
    drafts = [_draft(v, tmp_path) for v in "ABC"]
    for d in drafts:
        scene_drafts._judge_one(d, None)
    a, b, c = drafts
    assert (a.hard_issues, b.hard_issues) == (2, 0)
    assert c.failed and c.failure == "render failed"
    assert min((a, b), key=lambda d: d.sort_key) is b


def test_a_draft_preflight_cannot_judge_falls_back_to_a_small_render(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "1")
    monkeypatch.setattr(
        "manimgen.validator.preflight.preflight",
        lambda path, cls: PreflightResult(timed_out=True),
    )
    rendered = []
    monkeypatch.setattr(
        scene_drafts, "_render_one", lambda d, cues: rendered.append(d.variant)
    )
    scene_drafts._judge_one(_draft("A", tmp_path), None)
    assert rendered == ["A"]


def test_drafts_render_small_when_preflight_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MANIMGEN_PREFLIGHT", "0")
    rendered = []
    monkeypatch.setattr(
        scene_drafts, "_render_one", lambda d, cues: rendered.append(d.variant)
    )
    scene_drafts._judge_one(_draft("A", tmp_path), None)
    assert rendered == ["A"]
    assert os.path.exists(str(tmp_path / "section_01_draftA.py"))
