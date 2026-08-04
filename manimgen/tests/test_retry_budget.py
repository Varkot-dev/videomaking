"""Regression tests for B4 — the retry budget did not mean what its name says.

`MANIMGEN_MAX_RETRY_LLM_CALLS=2` reads like "this run makes at most 2 paid LLM
calls". It did not. It seeded *two independent per-category counters*
(error-repair and visual-repair), and both counters were local to
`retry_scene`, which `cli.py` calls once per section. So the real worst case was
roughly (error budget + visual budget) fix calls **per section**, plus a vision
call per visual attempt — a 10-section video could spend an order of magnitude
more than a reader predicts.

The fix does NOT silently redefine the per-category counters (that would change
behaviour for existing runs). Instead it adds a genuine **global per-run
budget** on top, defaulting high enough not to affect normal runs, and logs
consumption and exhaustion.

Also covered: the visual-fix path lacked the `seen_error_signatures` dedup the
error path has, so the same visual defect could burn repeated vision calls.
"""

import importlib

import pytest


@pytest.fixture
def retry_mod(monkeypatch):
    """Import retry.py fresh so module-level budget constants re-read env."""
    import manimgen.validator.retry as retry

    importlib.reload(retry)
    yield retry
    importlib.reload(retry)


# ---------------------------------------------------------------------------
# A global per-run budget must exist and be enforced
# ---------------------------------------------------------------------------


def test_global_budget_exists(retry_mod):
    assert hasattr(retry_mod, "MAX_TOTAL_LLM_CALLS"), (
        "no global per-run LLM budget — MANIMGEN_MAX_RETRY_LLM_CALLS only "
        "bounds per-category, per-section spend"
    )
    assert hasattr(retry_mod, "reset_run_budget")
    assert hasattr(retry_mod, "run_budget_used")


def test_global_budget_is_shared_across_sections(retry_mod, monkeypatch):
    """The whole point: spend in section 1 must reduce what section 2 can spend."""
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "3")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    # Simulate three sections each trying to spend one call.
    assert retry_mod._consume_run_budget("error") is True
    assert retry_mod._consume_run_budget("error") is True
    assert retry_mod._consume_run_budget("visual") is True
    # Budget of 3 is now exhausted — a fourth section gets nothing.
    assert retry_mod._consume_run_budget("error") is False, (
        "global budget did not carry across sections — each section got a "
        "fresh allowance, which is the bug"
    )
    assert retry_mod.run_budget_used() == 3


def test_global_budget_counts_both_categories(retry_mod, monkeypatch):
    """Error and visual calls draw from the SAME global pool."""
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "2")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    assert retry_mod._consume_run_budget("error") is True
    assert retry_mod._consume_run_budget("visual") is True
    assert retry_mod._consume_run_budget("error") is False, (
        "categories have separate global pools — the env var name still lies"
    )


def test_reset_run_budget_clears_the_counter(retry_mod, monkeypatch):
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "1")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    assert retry_mod._consume_run_budget("error") is True
    assert retry_mod._consume_run_budget("error") is False
    retry_mod.reset_run_budget()
    assert retry_mod._consume_run_budget("error") is True


def test_default_global_budget_is_generous(retry_mod):
    """Default must not change behaviour for normal-length runs."""
    importlib.reload(retry_mod)
    # Generous enough that a typical multi-section video never trips it.
    assert retry_mod.MAX_TOTAL_LLM_CALLS >= 50


# ---------------------------------------------------------------------------
# Budget consumption / exhaustion must be logged
# ---------------------------------------------------------------------------


def test_budget_exhaustion_is_logged(retry_mod, monkeypatch, caplog):
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "1")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    import logging

    caplog.set_level(logging.INFO)
    retry_mod._consume_run_budget("error")
    retry_mod._consume_run_budget("error")  # denied

    text = caplog.text.lower()
    assert "budget" in text
    assert "exhaust" in text or "exceeded" in text, (
        "budget exhaustion was silent — spend must be observable"
    )


def test_budget_consumption_is_logged(retry_mod, monkeypatch, caplog):
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "5")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    import logging

    caplog.set_level(logging.INFO)
    retry_mod._consume_run_budget("visual")

    assert "budget" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Per-category counters' scope must be explicit in the code
# ---------------------------------------------------------------------------


def test_per_category_scope_is_documented(retry_mod):
    """Someone reading the env var name must be able to predict the spend."""
    import inspect

    src = inspect.getsource(retry_mod)
    head = src[: src.index("class SceneErrorType")]
    assert "per-section" in head.lower(), (
        "the per-section scope of MAX_ERROR/VISUAL_LLM_FIX_CALLS is not stated "
        "where the constants are defined"
    )


def test_per_category_counter_names_state_their_scope(retry_mod):
    """The in-function counters should name their scope, not just their role."""
    import inspect

    src = inspect.getsource(retry_mod.retry_scene)
    assert "section_error_llm_calls_used" in src
    assert "section_visual_llm_calls_used" in src


# ---------------------------------------------------------------------------
# The global budget actually gates real LLM calls in retry_scene
# ---------------------------------------------------------------------------


def _stub_retry(retry_mod, monkeypatch, tmp_path, chat_calls, render_result):
    scene = tmp_path / "s.py"
    scene.write_text("from manimlib import *\n")

    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(
        retry_mod, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(retry_mod, "apply_error_aware_fixes", lambda c, e: (c, []))
    monkeypatch.setattr(retry_mod, "_write_attempt_artifacts", lambda *a: None)
    monkeypatch.setattr("manimgen.paths.logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(retry_mod, "_run_and_capture", lambda p, c: render_result)

    def _chat(**kwargs):
        chat_calls.append(kwargs)
        return "from manimlib import *\n# fixed\n"

    monkeypatch.setattr(retry_mod, "chat", _chat)
    return str(scene)


def test_exhausted_global_budget_blocks_error_fix_calls(
    retry_mod, monkeypatch, tmp_path
):
    """With the global budget spent, no paid error-fix call may be made."""
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "0")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    chat_calls: list = []
    # Every attempt fails with a DIFFERENT error so signature dedup never fires
    # — only the budget can stop the spend.
    counter = {"n": 0}

    def _capture(p, c):
        counter["n"] += 1
        return {
            "success": False,
            "video_path": None,
            "stderr": f"AttributeError: unique failure {counter['n']}",
        }

    scene = _stub_retry(retry_mod, monkeypatch, tmp_path, chat_calls, None)
    monkeypatch.setattr(retry_mod, "_run_and_capture", _capture)

    retry_mod.retry_scene({"id": "s1", "title": "T"}, "code", "MyScene", scene)

    assert chat_calls == [], (
        f"made {len(chat_calls)} paid LLM call(s) with a zero global budget"
    )


# ---------------------------------------------------------------------------
# Visual-fix dedup (the extra defect called out alongside B4)
# ---------------------------------------------------------------------------


def test_repeated_visual_defect_does_not_burn_repeated_vision_calls(
    retry_mod, monkeypatch, tmp_path
):
    """The same visual defect must not be sent to the vision model twice.

    The error path dedups via seen_error_signatures; the visual path had no
    equivalent, so an unfixable defect burned one paid vision call per attempt.
    """
    monkeypatch.setenv("MANIMGEN_MAX_TOTAL_LLM_CALLS", "999")
    monkeypatch.setenv("MANIMGEN_MAX_VISUAL_LLM_CALLS", "99")
    importlib.reload(retry_mod)
    retry_mod.reset_run_budget()

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")

    chat_calls: list = []
    scene = _stub_retry(
        retry_mod,
        monkeypatch,
        tmp_path,
        chat_calls,
        {"success": True, "video_path": str(video), "stderr": ""},
    )

    # frame_checker finds nothing; layout_checker keeps reporting the SAME defect.
    monkeypatch.setattr(
        "manimgen.validator.frame_checker.check_frames",
        lambda v: type("R", (), {"issues": []})(),
    )
    monkeypatch.setattr(
        retry_mod,
        "check_layout",
        lambda v: {
            "ok": False,
            "issues": "ISSUE: text overlaps | CAUSE: bad layout | FIX: move it",
            "frames": [],
            "skipped": False,
        },
    )

    retry_mod.retry_scene({"id": "s1", "title": "T"}, "code", "MyScene", scene)

    assert len(chat_calls) <= 1, (
        f"identical visual defect triggered {len(chat_calls)} paid vision "
        f"calls — the visual path has no dedup"
    )
