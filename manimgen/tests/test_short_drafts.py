"""Shorts: write two drafts of the scene, keep the better one.

Everything here is mocked (no LLM, no manimgl) except the last test, which
renders two tiny scenes for real under a display (xvfb-run on Linux).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import sys
import threading

import pytest

from manimgen import cli, paths
from manimgen.generator import scene_drafts
from manimgen.llm import PaidApiBlockedError
from manimgen.probes.overlap_report import Overlap
from manimgen.types import GateResult
from manimgen.utils import draft_class_name, section_class_name
from manimgen.validator import render_command
from manimgen.validator.frame_checker import FrameCheckResult

SECTION = {"id": "section_01", "title": "T"}
BASE = section_class_name(SECTION)


def _scene(cls: str, extra: str = "") -> str:
    return (
        "from manimlib import *\n\n\n"
        f"class {cls}(Scene):\n    def construct(self):\n        pass{extra}\n"
    )


@pytest.fixture
def scenes(tmp_path, monkeypatch):
    monkeypatch.setitem(paths._PATHS, "scenes", str(tmp_path / "scenes"))
    monkeypatch.setattr(
        "manimgen.validator.frame_checker.check_frames",
        lambda video: FrameCheckResult(),
    )
    return tmp_path / "scenes"


def _gate_factory(scenes_dir, codes=None, blocked=None, errors=None):
    """gate_one(variant) that writes a draft file like generate_scenes does."""
    codes = codes or {}
    blocked = blocked or {}
    errors = errors or {}
    seen = []

    def gate_one(variant):
        seen.append((variant, threading.current_thread().name))
        if variant in errors:
            raise errors[variant]
        cls = draft_class_name(SECTION, variant)
        code = codes.get(variant, _scene(cls))
        os.makedirs(scenes_dir, exist_ok=True)
        path = scenes_dir / f"section_01_draft{variant}.py"
        path.write_text(code, encoding="utf-8")
        return GateResult(
            code=code,
            class_name=cls,
            scene_path=str(path),
            timing_blocked=blocked.get(variant) == "timing",
            precheck_blocked=blocked.get(variant) == "precheck",
        )

    gate_one.seen = seen
    return gate_one


def _fake_render(monkeypatch, tmp_path, outcomes, calls=None):
    """Patch run_manimgl: outcomes maps class name -> RenderResult kwargs."""

    def fake(scene_path, class_name, timeout=None, draft=None):
        if calls is not None:
            calls.append((class_name, timeout, draft))
        spec = outcomes[class_name]
        if spec == "fail":
            return render_command.RenderResult(False, None, "", "boom", 1, False)
        if spec == "timeout":
            return render_command.RenderResult(False, None, "", "slow", None, True)
        video = tmp_path / f"{class_name}.mp4"
        video.write_bytes(b"x")
        return render_command.RenderResult(
            True, str(video), "", "", 0, False, overlaps=tuple(spec)
        )

    monkeypatch.setattr(render_command, "run_manimgl", fake)


def _overlap(n=1):
    return [Overlap(1.0, f"a{i}", f"b{i}", 0.9) for i in range(n)]


A = draft_class_name(SECTION, "A")
B = draft_class_name(SECTION, "B")


# --- config and size flags -------------------------------------------------


def test_drafts_default_config_and_env(monkeypatch):
    monkeypatch.delenv("MANIMGEN_SHORT_DRAFTS", raising=False)
    monkeypatch.setitem(paths._SHORTS, "drafts", 2)
    assert paths.short_drafts() == 2
    monkeypatch.setitem(paths._SHORTS, "drafts", 1)
    assert paths.short_drafts() == 1
    monkeypatch.setenv("MANIMGEN_SHORT_DRAFTS", "3")
    assert paths.short_drafts() == 3
    monkeypatch.setenv("MANIMGEN_SHORT_DRAFTS", "99")
    assert paths.short_drafts() == paths.MAX_SHORT_DRAFTS
    monkeypatch.setenv("MANIMGEN_SHORT_DRAFTS", "junk")
    assert paths.short_drafts() == 1


def test_shipped_config_enables_two_drafts():
    from manimgen import config

    assert config.DEFAULTS["shorts"]["drafts"] == 2


def test_draft_size_flags_do_not_touch_global_state():
    paths.set_video_format("short")
    assert paths.is_draft() is False
    assert paths.render_size_flags(draft=True) == ["-r", "540x960"]
    assert paths.render_size_flags() == ["-r", "1080x1920"]
    assert paths.render_resolution(draft=True) == "540x960"
    assert paths.is_draft() is False
    cmd = render_command.build_manimgl_command("s.py", "X", draft=True)
    assert cmd[cmd.index("-r") + 1] == "540x960"
    assert paths.is_draft() is False


def test_landscape_flags_unchanged():
    assert paths.render_size_flags() == [paths.render_quality_flag()]
    small = paths.render_size_flags(draft=True)
    assert small == ["-r", "960x540"]
    assert paths.render_resolution(draft=True) == "960x540"
    assert paths.is_draft() is False


def test_explicit_per_call_value_beats_global_draft():
    paths.set_video_format("short")
    paths.set_draft(True)
    assert paths.render_size_flags(draft=False) == ["-r", "1080x1920"]
    assert paths.render_size_flags() == ["-r", "540x960"]


def test_draft_class_name_is_not_a_substring_of_or_contains_final():
    for v in "ABC":
        name = draft_class_name(SECTION, v)
        assert BASE not in name
        assert name not in BASE


# --- scoring and picking ---------------------------------------------------


def test_drafts_written_concurrently(scenes, monkeypatch, tmp_path):
    barrier = threading.Barrier(2, timeout=10)
    inner = _gate_factory(scenes)

    def gate_one(variant):
        barrier.wait()  # both drafts must be in flight at once
        return inner(variant)

    _fake_render(monkeypatch, tmp_path, {A: [], B: []})
    scene_drafts.pick_best_draft(SECTION, None, gate_one, 2)
    assert {v for v, _ in inner.seen} == {"A", "B"}
    assert len({t for _, t in inner.seen}) == 2


def test_drafts_rendered_concurrently_small_with_short_timeout(
    scenes, monkeypatch, tmp_path
):
    barrier = threading.Barrier(2, timeout=10)
    calls = []

    def fake(scene_path, class_name, timeout=None, draft=None):
        calls.append((class_name, timeout, draft))
        barrier.wait()
        video = tmp_path / f"{class_name}.mp4"
        video.write_bytes(b"x")
        return render_command.RenderResult(True, str(video), "", "", 0, False)

    monkeypatch.setattr(render_command, "run_manimgl", fake)
    scene_drafts.pick_best_draft(SECTION, None, _gate_factory(scenes), 2)
    assert sorted(c[0] for c in calls) == [A, B]
    assert all(c[2] is True for c in calls)
    assert all(0 < c[1] <= 600 for c in calls)
    assert paths.is_draft() is False


def test_fewer_hard_issues_wins_and_is_promoted(scenes, monkeypatch, tmp_path, caplog):
    # A has two overlaps, B is clean: B wins even though A comes first.
    _fake_render(monkeypatch, tmp_path, {A: _overlap(2), B: []})
    caplog.set_level(logging.INFO)
    gate = scene_drafts.pick_best_draft(SECTION, None, _gate_factory(scenes), 2)
    assert gate.class_name == BASE
    assert gate.scene_path == str(scenes / "section_01.py")
    text = (scenes / "section_01.py").read_text(encoding="utf-8")
    assert f"class {BASE}(" in text and "Draft" not in text
    assert gate.code == text
    line = next(r.message for r in caplog.records if "[drafts]" in r.message)
    assert line.startswith("[drafts] section_01: A score=2/0/")
    assert ", B score=0/0/" in line and "picked B" in line
    # draft scene files stay for inspection, draft videos are removed
    assert (scenes / "section_01_draftA.py").exists()
    assert not (tmp_path / f"{A}.mp4").exists()
    rec = json.loads(
        (
            paths.logs_dir()
            and open(
                os.path.join(paths.logs_dir(), scene_drafts.DRAFTS_LOG_NAME),
                encoding="utf-8",
            )
            .read()
            .splitlines()[-1]
        )
    )
    assert rec["picked"] == "B" and rec["drafts"][0]["hard_issues"] == 2


def test_tie_breaks_on_timing_warnings_then_length(scenes, monkeypatch, tmp_path):
    short, long_ = _scene(A), _scene(B, "\n        x = 1  # padding padding")
    # equal hard issues and timing: the shorter code wins
    _fake_render(monkeypatch, tmp_path, {A: [], B: []})
    gate = scene_drafts.pick_best_draft(
        SECTION,
        None,
        _gate_factory(
            scenes, codes={"A": long_.replace(B, A), "B": short.replace(A, B)}
        ),
        2,
    )
    assert "x = 1" not in gate.code

    d1 = scene_drafts.Draft("A", hard_issues=0, timing_warnings=1, code_chars=10)
    d2 = scene_drafts.Draft("B", hard_issues=0, timing_warnings=0, code_chars=999)
    d3 = scene_drafts.Draft("C", hard_issues=1, timing_warnings=0, code_chars=1)
    d4 = scene_drafts.Draft("D", failure="render failed")
    ranked = sorted([d4, d3, d1, d2], key=lambda d: d.sort_key)
    assert [d.variant for d in ranked] == ["B", "A", "C", "D"]


def test_render_failure_and_gate_rejection_are_worst(scenes, monkeypatch, tmp_path):
    _fake_render(monkeypatch, tmp_path, {A: "fail", B: _overlap(5)})
    gate = scene_drafts.pick_best_draft(SECTION, None, _gate_factory(scenes), 2)
    assert "DraftB" not in gate.code  # B (five overlaps) still beats a failed A
    assert (scenes / "section_01.py").exists()

    # a rejected draft is not rendered at all
    calls = []
    _fake_render(monkeypatch, tmp_path, {B: []}, calls)
    gate = scene_drafts.pick_best_draft(
        SECTION, None, _gate_factory(scenes, blocked={"A": "precheck"}), 2
    )
    assert [c[0] for c in calls] == [B]
    assert gate.precheck_blocked is False


def test_timeout_scores_as_failure(scenes, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _fake_render(monkeypatch, tmp_path, {A: "timeout", B: []})
    scene_drafts.pick_best_draft(SECTION, None, _gate_factory(scenes), 2)
    line = next(r.message for r in caplog.records if "[drafts]" in r.message)
    assert "A score=failed (render timed out)" in line and "picked B" in line


def test_both_fail_falls_back_to_first_draft_unchanged(scenes, monkeypatch, tmp_path):
    _fake_render(monkeypatch, tmp_path, {A: "fail", B: "timeout"})
    gate = scene_drafts.pick_best_draft(SECTION, None, _gate_factory(scenes), 2)
    assert gate.class_name == BASE
    assert gate.scene_path == str(scenes / "section_01.py")
    assert f"class {BASE}(" in gate.code
    assert gate.timing_blocked is False and gate.precheck_blocked is False


def test_both_blocked_keeps_first_drafts_flags_for_retry(scenes, monkeypatch, tmp_path):
    calls = []
    _fake_render(monkeypatch, tmp_path, {}, calls)
    gate = scene_drafts.pick_best_draft(
        SECTION,
        None,
        _gate_factory(scenes, blocked={"A": "timing", "B": "precheck"}),
        2,
    )
    assert calls == []
    assert gate.timing_blocked is True and gate.precheck_blocked is False


def test_one_draft_error_uses_the_other(scenes, monkeypatch, tmp_path):
    _fake_render(monkeypatch, tmp_path, {B: []})
    gate = scene_drafts.pick_best_draft(
        SECTION, None, _gate_factory(scenes, errors={"A": RuntimeError("x")}), 2
    )
    assert gate.class_name == BASE


def test_all_drafts_error_raises_first_error(scenes):
    errs = {"A": RuntimeError("first"), "B": RuntimeError("second")}
    with pytest.raises(RuntimeError, match="first"):
        scene_drafts.pick_best_draft(
            SECTION, None, _gate_factory(scenes, errors=errs), 2
        )


def test_usage_stop_is_raised_even_if_the_other_draft_is_fine(scenes):
    errs = {"B": PaidApiBlockedError("blocked")}
    with pytest.raises(PaidApiBlockedError):
        scene_drafts.pick_best_draft(
            SECTION, None, _gate_factory(scenes, errors=errs), 2
        )


# --- wiring in cli ---------------------------------------------------------


def _fake_generate(scenes_dir, calls):
    def fake(section, cue_durations=None, overview=None, variant=None):
        calls.append(variant)
        cls = (
            draft_class_name(section, variant)
            if variant
            else section_class_name(section)
        )
        name = f"section_01_draft{variant}.py" if variant else "section_01.py"
        os.makedirs(scenes_dir, exist_ok=True)
        code = _scene(cls)
        (scenes_dir / name).write_text(code, encoding="utf-8")
        return code, cls, str(scenes_dir / name)

    return fake


def test_cli_drafts_one_is_todays_single_call(scenes, monkeypatch):
    paths.set_video_format("short")
    monkeypatch.setitem(paths._SHORTS, "drafts", 1)
    monkeypatch.delenv("MANIMGEN_SHORT_DRAFTS", raising=False)
    calls = []
    monkeypatch.setattr(cli, "generate_scenes", _fake_generate(scenes, calls))
    gate = cli._generate_and_gate(SECTION, None, None)
    assert calls == [None]
    assert gate.class_name == BASE


def test_cli_landscape_ignores_drafts(scenes, monkeypatch):
    monkeypatch.setitem(paths._SHORTS, "drafts", 2)
    monkeypatch.delenv("MANIMGEN_SHORT_DRAFTS", raising=False)
    calls = []
    monkeypatch.setattr(cli, "generate_scenes", _fake_generate(scenes, calls))
    cli._generate_and_gate(SECTION, None, None)
    assert calls == [None]


def test_cli_short_with_two_drafts_picks_the_clean_one(scenes, monkeypatch, tmp_path):
    paths.set_video_format("short")
    monkeypatch.setitem(paths._SHORTS, "drafts", 2)
    monkeypatch.delenv("MANIMGEN_SHORT_DRAFTS", raising=False)
    calls = []
    monkeypatch.setattr(cli, "generate_scenes", _fake_generate(scenes, calls))
    _fake_render(monkeypatch, tmp_path, {A: _overlap(1), B: []})
    gate = cli._generate_and_gate(SECTION, None, None)
    assert sorted(calls) == ["A", "B"]
    assert gate.class_name == BASE
    assert gate.scene_path == str(scenes / "section_01.py")


def test_resume_with_fresh_render_does_not_write_drafts(monkeypatch, tmp_path):
    paths.set_video_format("short")
    monkeypatch.setitem(paths._SHORTS, "drafts", 2)
    video = tmp_path / f"{BASE}.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(cli, "_find_rendered_video", lambda name: str(video))
    monkeypatch.setattr(cli, "_render_is_fresh", lambda v, k: True)
    monkeypatch.setattr(cli, "_all_cues_muxed", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_cached_outcome", lambda v: (cli.SectionStatus.OK, ""))

    def boom(*a, **k):
        raise AssertionError("must not regenerate")

    monkeypatch.setattr(cli, "_generate_and_gate", boom)
    monkeypatch.setattr(cli, "generate_scenes", boom)
    outcome = cli._run_section(SECTION, 1, False, "hash", section_audio=None)
    assert outcome.status == cli.SectionStatus.OK


def test_section_key_does_not_depend_on_drafts(monkeypatch):
    key = cli._section_key(SECTION, "h", [1.0])
    monkeypatch.setitem(paths._SHORTS, "drafts", 3)
    assert cli._section_key(SECTION, "h", [1.0]) == key


# --- real render -----------------------------------------------------------


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform != "win32"


_CLEAN = """from manimlib import *


class {cls}(Scene):
    def construct(self):
        a = Text("first caption", font_size=28).move_to([-0.3, 1.5, 0])
        b = Text("second caption", font_size=28).move_to([-0.3, 0.0, 0])
        self.play(FadeIn(a), FadeIn(b), run_time=0.3)
        self.wait(0.3)
"""

_OVERLAPPING = """from manimlib import *


class {cls}(Scene):
    def construct(self):
        a = Text("first caption", font_size=28).move_to([-0.3, 1.5, 0])
        b = Text("second caption", font_size=28).move_to([-0.2, 1.5, 0])
        self.play(FadeIn(a), FadeIn(b), run_time=0.3)
        self.wait(0.3)
"""


@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.skipif(
    importlib.util.find_spec("manimlib") is None
    or shutil.which("manimgl") is None
    or not _has_display(),
    reason="needs manimgl and a display (xvfb-run on Linux)",
)
def test_real_render_picks_the_clean_draft(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANIMGEN_OVERLAP_PROBE", raising=False)
    monkeypatch.setitem(paths._PATHS, "scenes", str(tmp_path / "scenes"))
    paths.set_video_format("short")
    sources = {"A": _OVERLAPPING, "B": _CLEAN}
    scenes_dir = tmp_path / "scenes"
    seen_sizes = []

    real = render_command.run_manimgl

    def spy(scene_path, class_name, timeout=None, draft=None):
        seen_sizes.append(draft)
        return real(scene_path, class_name, timeout=timeout, draft=draft)

    monkeypatch.setattr(render_command, "run_manimgl", spy)

    def gate_one(variant):
        cls = draft_class_name(SECTION, variant)
        code = sources[variant].format(cls=cls)
        scenes_dir.mkdir(exist_ok=True)
        path = scenes_dir / f"section_01_draft{variant}.py"
        path.write_text(code, encoding="utf-8")
        return GateResult(code, cls, str(path), False)

    gate = scene_drafts.pick_best_draft(SECTION, None, gate_one, 2)
    assert seen_sizes == [True, True]
    assert paths.is_draft() is False
    assert "second caption" in gate.code
    assert "-0.2" not in gate.code  # the overlapping draft lost
    assert gate.class_name == BASE
    rec = json.loads(
        open(
            os.path.join(paths.logs_dir(), scene_drafts.DRAFTS_LOG_NAME),
            encoding="utf-8",
        )
        .read()
        .splitlines()[-1]
    )
    assert rec["picked"] == "B"
    by = {d["variant"]: d for d in rec["drafts"]}
    assert by["A"]["failed"] is False and by["A"]["hard_issues"] >= 1
    assert by["B"]["hard_issues"] == 0
