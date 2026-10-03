"""R11: an error-aware local fix that stops the scene compiling must be discarded.

retry_scene wrote apply_error_aware_fixes' output back with no syntax check, so a
rewrite that produced `f(buff=1, buff=2)` replaced a scene the LLM could still
have repaired. Leaf seams only: the render, the LLM and the logs dir are faked.
"""

import pytest

import manimgen.validator.retry as retry

GOOD = (
    "from manimlib import *\n\n\n"
    "class S(Scene):\n"
    "    def construct(self):\n"
    "        self.wait(1)\n"
)
CORRUPT = GOOD + "f(a=1, a=2)\n"
LLM_FIXED = GOOD + "# fixed by llm\n"


def test_breaks_compile_helper():
    assert retry._breaks_compile(GOOD, CORRUPT) is True
    assert retry._breaks_compile(GOOD, GOOD + "x = 1\n") is False
    # already-broken input: a partial fix is still progress, never discarded
    assert retry._breaks_compile("def f(:\n", CORRUPT) is False


@pytest.fixture
def seams(monkeypatch, tmp_path):
    monkeypatch.setattr(retry.paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(retry, "MAX_RETRIES", 3)
    monkeypatch.setattr(retry, "_load_retry_system_prompt", lambda: "sys")
    retry.reset_run_budget()
    calls = {"chat": 0, "render": 0}

    def fake_run(scene_path, class_name):
        calls["render"] += 1
        return {"success": False, "video_path": None, "stderr": "TypeError: boom"}

    def fake_chat(**kwargs):
        calls["chat"] += 1
        return LLM_FIXED

    monkeypatch.setattr(retry, "_run_and_capture", fake_run)
    monkeypatch.setattr(retry, "chat", fake_chat)
    monkeypatch.setattr(
        retry, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    return calls


def test_corrupting_local_fix_is_discarded_and_llm_fix_runs(
    monkeypatch, tmp_path, seams
):
    monkeypatch.setattr(
        retry, "apply_error_aware_fixes", lambda code, stderr: (CORRUPT, ["bad rule"])
    )
    scene = tmp_path / "s.py"
    scene.write_text(GOOD, encoding="utf-8")
    ok, _ = retry.retry_scene({"id": "section_01"}, GOOD, "S", str(scene))
    assert ok is False
    assert seams["chat"] >= 1, "a discarded local fix must fall through to the LLM fix"
    assert "a=1, a=2" not in scene.read_text(encoding="utf-8")


def test_compilable_local_fix_is_still_applied_without_llm(
    monkeypatch, tmp_path, seams, capsys
):
    fixed = GOOD + "x = 1\n"

    def local(code, stderr):
        # one deterministic fix, then nothing left to fix
        return (fixed, ["good rule"]) if code == GOOD else (code, [])

    monkeypatch.setattr(retry, "apply_error_aware_fixes", local)
    scene = tmp_path / "s.py"
    scene.write_text(GOOD, encoding="utf-8")
    retry.retry_scene({"id": "section_01"}, GOOD, "S", str(scene))
    out = capsys.readouterr().out
    assert "Attempt 1/3 applied local fixes: good rule" in out
    assert "discarded" not in out
