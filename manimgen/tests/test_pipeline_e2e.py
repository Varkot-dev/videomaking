"""
Focused, FULLY-MOCKED cli.py / pipeline integration tests.

History (#35): this file used to hold a single brittle full-`main()` e2e test
marked ``xfail(strict)``. Its xfail reason named ``manimgen.cli.check_layout``
as the thing to mock — but that symbol DOES NOT EXIST. The real layout-check
call lives in ``manimgen.validator.layout_checker.check_layout`` and is invoked
from ``validator.retry`` and ``validator.render_validator`` (never from
``cli``). Naively "un-xfailing" the old test would have fired a PAID Gemini
vision call through that real path.

Replacement: instead of one fragile end-to-end test, these are focused
integration tests at the documented seams — ``chat()`` (LLM) and per-module
``subprocess.run`` (manimgl/ffmpeg). EVERY LLM, subprocess, and layout-checker
call is mocked. There is ZERO real network/LLM/Gemini/subprocess work here.

If you add a test to this file: mock ``chat`` (in scene_generator AND/OR
lesson_planner), mock ``subprocess.run`` at the module that calls it, and never
let ``layout_checker.check_layout`` run unmocked.
"""

import importlib
import inspect

import pytest

import manimgen.cli as cli
from manimgen.validator import fallback as fallback_mod
from manimgen.validator import runner as runner_mod


# ── Seam 1: planner LLM output flows into the Director (scene-gen) ───────────


@pytest.mark.integration
def test_planner_output_flows_into_scene_generator(mocker, tmp_path):
    """plan_lesson's parsed sections drive generate_scenes at the chat() seam.

    Both LLM calls are mocked: lesson_planner.chat returns a storyboard JSON,
    scene_generator.chat returns ManimGL code. We assert the section the
    planner produced is the section the Director is asked to generate — the
    planner→Director wiring — without any API call. The scene is written to a
    tmp scenes dir (real round-trip; generate_scenes re-reads the file), so no
    global ``open`` mock is needed.
    """
    plan_json = (
        '{"title": "Linear Search", "sections": ['
        '{"id": "section_01", "title": "Scan", '
        '"narration": "We scan left to right.", '
        '"cue_word_indices": [0], '
        '"cues": [{"index": 0, "visual": "array of boxes"}]}]}'
    )
    mocker.patch("manimgen.planner.lesson_planner.chat", return_value=plan_json)
    # Avoid disk reads for the Director's few-shot reference frames/examples.
    mocker.patch(
        "manimgen.generator.scene_generator.load_reference_frames", return_value=[]
    )
    mocker.patch(
        "manimgen.generator.scene_generator._load_examples_text", return_value=""
    )
    # Write the generated scene into an isolated tmp dir.
    mocker.patch(
        "manimgen.generator.scene_generator.paths.scenes_dir",
        return_value=str(tmp_path),
    )
    scene_chat = mocker.patch(
        "manimgen.generator.scene_generator.chat",
        return_value="from manimlib import *\n\n\nclass ScanScene(Scene):\n    def construct(self):\n        self.wait(1)\n",
    )

    from manimgen.generator.scene_generator import generate_scenes
    from manimgen.planner.lesson_planner import plan_lesson

    plan = plan_lesson("linear search")
    assert plan["sections"][0]["title"] == "Scan"

    code, class_name, _ = generate_scenes(plan["sections"][0], cue_durations=[2.0])

    # Director was invoked exactly once, at the chat() seam, with the planner's
    # storyboard text in the user message.
    assert scene_chat.call_count == 1
    user_msg = scene_chat.call_args.kwargs["user"]
    assert "array of boxes" in user_msg
    assert class_name == "Section01Scene"
    assert code.startswith("from manimlib")


# ── Seam 2: run_scene wires the manimgl subprocess and returns the video ─────


@pytest.mark.integration
def test_run_scene_invokes_manimgl_subprocess(mocker, tmp_path):
    """run_scene shells out to manimgl with the dark-bg flag, mocked.

    subprocess.run is replaced; we assert the manimgl argv is correct and that
    a returncode==0 yields the discovered video path. No real render.
    """
    scene_path = tmp_path / "section_01.py"
    scene_path.write_text("from manimlib import *\n\n\nclass S(Scene):\n    pass\n")

    fake_proc = mocker.MagicMock(returncode=0, stdout="ok", stderr="")
    run_mock = mocker.patch(
        "manimgen.validator.runner.subprocess.run", return_value=fake_proc
    )
    mocker.patch(
        "manimgen.validator.runner.precheck_and_autofix_file",
        return_value={"ok": True, "applied_fixes": [], "layout_warnings": []},
    )
    mocker.patch(
        "manimgen.validator.runner._find_rendered_video",
        return_value="/tmp/Section01Scene.mp4",
    )

    success, video = runner_mod.run_scene(str(scene_path), "Section01Scene")

    assert success is True
    assert video == "/tmp/Section01Scene.mp4"
    argv = run_mock.call_args.args[0]
    assert argv[0] == "manimgl"
    # Dark background contract: -c "#1C1C1C" (never --background_color).
    assert "-c" in argv and "#1C1C1C" in argv
    assert "--background_color" not in argv


@pytest.mark.integration
def test_run_scene_returns_failure_on_nonzero_exit(mocker, tmp_path):
    """A non-zero manimgl exit yields (False, None) without raising."""
    scene_path = tmp_path / "section_01.py"
    scene_path.write_text("from manimlib import *\n\n\nclass S(Scene):\n    pass\n")

    fake_proc = mocker.MagicMock(returncode=1, stdout="", stderr="boom")
    mocker.patch(
        "manimgen.validator.runner.subprocess.run", return_value=fake_proc
    )
    mocker.patch(
        "manimgen.validator.runner.precheck_and_autofix_file",
        return_value={"ok": True, "applied_fixes": [], "layout_warnings": []},
    )
    find = mocker.patch("manimgen.validator.runner._find_rendered_video")

    success, video = runner_mod.run_scene(str(scene_path), "Section01Scene")

    assert success is False
    assert video is None
    find.assert_not_called()  # never look for a video when the render failed


# ── Seam 3: fallback_scene writes code + invokes manimgl (no LLM) ────────────


@pytest.mark.integration
def test_fallback_scene_renders_without_llm(mocker, tmp_path):
    """fallback_scene is deterministic — it must never call chat()/the LLM."""
    mocker.patch(
        "manimgen.validator.fallback.paths.scenes_dir", return_value=str(tmp_path)
    )
    fake_proc = mocker.MagicMock(returncode=0, stdout="ok", stderr="")
    run_mock = mocker.patch(
        "manimgen.validator.fallback.subprocess.run", return_value=fake_proc
    )
    mocker.patch(
        "manimgen.validator.runner._find_rendered_video",
        return_value="/tmp/Section01FallbackScene.mp4",
    )

    section = {"id": "section_01", "title": "Recap", "narration": "Quick recap."}
    out = fallback_mod.fallback_scene(section)

    assert out == "/tmp/Section01FallbackScene.mp4"
    argv = run_mock.call_args.args[0]
    assert argv[0] == "manimgl"
    # Scene file was actually written to the (mocked) scenes dir.
    written = list(tmp_path.glob("*_fallback.py"))
    assert written, "fallback scene .py was not written"


# ── Seam 4: cli._run_section error path → fallback when render+retry fail ────


@pytest.mark.integration
def test_run_section_uses_fallback_when_render_and_retry_fail(mocker):
    """cli._run_section routes to fallback_scene when render + retry both fail.

    TTS is off, so no audio/cut/mux happens. generate_scenes, run_scene,
    retry_scene, and fallback_scene are all mocked — zero LLM, zero subprocess.
    """
    mocker.patch(
        "manimgen.cli.generate_scenes",
        return_value=("from manimlib import *\n", "Section01Scene", "/tmp/s.py"),
    )
    mocker.patch("manimgen.cli._find_rendered_video", return_value=None)
    mocker.patch("manimgen.cli.run_scene", return_value=(False, None))
    retry = mocker.patch("manimgen.cli.retry_scene", return_value=(False, None))
    fb = mocker.patch(
        "manimgen.cli.fallback_scene", return_value="/tmp/fallback.mp4"
    )
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("manimgen.cli._write_hash_sidecar")

    section = {"id": "section_01", "title": "Scan", "narration": "scan."}
    out = cli._run_section(section, 1, tts_on=False, current_topic_hash="deadbeef")

    assert out == ["/tmp/fallback.mp4"]
    retry.assert_called_once()
    fb.assert_called_once_with(section)


@pytest.mark.integration
def test_run_section_returns_empty_when_fallback_also_fails(mocker):
    """If even fallback yields no video, the section is dropped (empty list)."""
    mocker.patch(
        "manimgen.cli.generate_scenes",
        return_value=("from manimlib import *\n", "Section01Scene", "/tmp/s.py"),
    )
    mocker.patch("manimgen.cli._find_rendered_video", return_value=None)
    mocker.patch("manimgen.cli.run_scene", return_value=(False, None))
    mocker.patch("manimgen.cli.retry_scene", return_value=(False, None))
    mocker.patch("manimgen.cli.fallback_scene", return_value=None)

    section = {"id": "section_01", "title": "Scan", "narration": "scan."}
    out = cli._run_section(section, 1, tts_on=False, current_topic_hash="deadbeef")

    assert out == []


# ── Regression: the xfail reason's dead symbol claim is now accurate ─────────


@pytest.mark.unit
def test_check_layout_symbol_lives_in_layout_checker_not_cli():
    """Locks the corrected #35 claim: the real check_layout is NOT on cli.

    The old xfail reason told future readers to mock ``manimgen.cli.check_layout``
    — a symbol that never existed, so the mock would no-op and a real paid
    Gemini vision call would fire through the genuine path. This test pins the
    truth so the comment can't silently rot back: check_layout is defined in
    layout_checker and consumed by retry / render_validator; cli has no such
    attribute and no layout import.
    """
    from manimgen.validator import layout_checker

    assert hasattr(layout_checker, "check_layout")
    assert not hasattr(cli, "check_layout")

    cli_src = inspect.getsource(importlib.import_module("manimgen.cli"))
    assert "check_layout" not in cli_src

    # The real consumers import it from layout_checker.
    from manimgen.validator import render_validator, retry

    assert render_validator.check_layout is layout_checker.check_layout
    assert retry.check_layout is layout_checker.check_layout
