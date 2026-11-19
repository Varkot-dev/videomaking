"""Parallel segmented rendering (validator/segmented_render.py).

Unit tests cover the arithmetic (segment count, split points, core budget,
timeouts, probe-report merge, config and env) and the fallbacks with a faked
process layer. The integration tests at the bottom render real scenes under
xvfb: segmented against single renders (frame counts, pixels at the joins,
merged probe report, speedup).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from manimgen import paths, procutil
from manimgen.probes import overlap_probe, overlap_report
from manimgen.validator import render_command
from manimgen.validator import segmented_render as sr

# ---------------------------------------------------------------------------
# Unit: how many segments, where to cut
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoreBudget:
    def test_all_but_one_core(self):
        assert sr.core_budget(4) == 3
        assert sr.core_budget(8) == 7
        assert sr.core_budget(1) == 1
        assert sr.core_budget(2) == 1

    def test_auto_uses_the_budget(self):
        assert sr.segments_wanted(None, cpus=4) == 3
        assert sr.segments_wanted(None, cpus=1) == 1

    def test_auto_splits_the_budget_between_parallel_sections(self):
        assert sr.segments_wanted(None, cpus=8, concurrent_renders=3) == 2
        assert sr.segments_wanted(None, cpus=4, concurrent_renders=3) == 1
        assert sr.segments_wanted(None, cpus=4, concurrent_renders=2) == 1
        assert sr.segments_wanted(None, cpus=16, concurrent_renders=3) == 5

    def test_explicit_setting_is_used_as_given(self):
        assert sr.segments_wanted(4, cpus=2, concurrent_renders=3) == 4
        assert sr.segments_wanted(1, cpus=16) == 1

    def test_hold_grants_only_free_cores_and_releases(self):
        budget = sr.CoreBudget()
        with budget.hold(3, 3) as first:
            assert first == 3
            with budget.hold(3, 3) as second:
                assert second == 0
            with budget.hold(1, 1, minimum=1) as single:
                assert single == 1  # a single render always runs
                assert budget.in_use == 4
        assert budget.in_use == 0

    def test_hold_partial_grant(self):
        budget = sr.CoreBudget()
        with budget.hold(2, 3):
            with budget.hold(3, 3) as rest:
                assert rest == 1


@pytest.mark.unit
class TestSegmentCount:
    def test_short_scenes_are_not_split(self):
        assert sr.segment_count(3, 20, 7.9) == 1

    def test_limited_by_animations_and_length(self):
        assert sr.segment_count(3, 2, 40.0) == 2
        assert sr.segment_count(3, 1, 40.0) == 1
        assert sr.segment_count(8, 50, 10.0) == 3  # one per 3 s of video
        assert sr.segment_count(3, 50, 43.0) == 3

    def test_one_wanted_means_one(self):
        assert sr.segment_count(1, 50, 60.0) == 1


@pytest.mark.unit
class TestSplitPlays:
    def test_balanced_by_duration_not_by_animation_count(self):
        # nine animations; the first five are short, the rest long
        times = [1, 2, 3, 4, 5, 11, 17, 23, 29]
        ranges = sr.split_plays([float(t) for t in times], 2)
        assert ranges == [(0, 7), (7, None)]
        shares = sr.segment_shares([float(t) for t in times], ranges)
        assert abs(shares[0] - 0.5) < 0.15

    def test_ranges_cover_every_animation_once(self):
        times = [float(i) * 1.3 for i in range(1, 31)]
        for k in (2, 3, 5):
            ranges = sr.split_plays(times, k)
            assert ranges[0][0] == 0 and ranges[-1][1] is None
            for (_, end), (start, _) in zip(ranges, ranges[1:]):
                assert end == start
            assert all((e is None or e > s) for s, e in ranges), "no empty segment"
            assert sum(sr.segment_shares(times, ranges)) == pytest.approx(1.0)

    def test_fewer_animations_than_segments(self):
        assert sr.split_plays([1.0, 2.0], 5) == [(0, 1), (1, None)]
        assert sr.split_plays([3.0], 3) == [(0, None)]

    def test_one_huge_wait_is_not_cut(self):
        ranges = sr.split_plays([0.5, 1.0, 31.0, 31.5], 3)
        shares = sr.segment_shares([0.5, 1.0, 31.0, 31.5], ranges)
        assert max(shares) > sr.MAX_SEGMENT_SHARE  # the caller falls back

    def test_command_uses_manimgl_n_and_its_own_folder(self):
        cmd = sr.segment_command("s.py", "Scene1", None, 3, 9, "/tmp/x")
        assert cmd[:3] == ["manimgl", "s.py", "Scene1"]
        assert cmd[cmd.index("-n") + 1] == "3,9"
        assert cmd[cmd.index("--video_dir") + 1] == "/tmp/x"
        last = sr.segment_command("s.py", "Scene1", None, 9, None, "/tmp/y")
        assert last[last.index("-n") + 1] == "9"
        assert sr.segment_file_name("Scene1", 3, 9) == "Scene1_3_9.mp4"
        assert sr.segment_file_name("Scene1", 9, None) == "Scene1_9.mp4"


@pytest.mark.unit
class TestTimeout:
    def test_scales_with_segment_length(self):
        whole = 460.0
        small = sr.segment_timeout(whole, 0.2)
        large = sr.segment_timeout(whole, 0.6)
        assert 60 < small < large <= whole

    def test_never_above_the_whole_budget(self):
        assert sr.segment_timeout(100.0, 1.0) == 100.0
        assert sr.segment_timeout(30.0, 0.5) <= 30.0


# ---------------------------------------------------------------------------
# Unit: config and environment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetting:
    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setitem(paths._RENDERING, "parallel_segments", "auto")
        monkeypatch.delenv("MANIMGEN_PARALLEL_SEGMENTS", raising=False)
        assert paths.render_parallel_segments() is None
        monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "1")
        assert paths.render_parallel_segments() == 1
        monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "4")
        assert paths.render_parallel_segments() == 4
        monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "auto")
        assert paths.render_parallel_segments() is None

    def test_config_value(self, monkeypatch):
        monkeypatch.delenv("MANIMGEN_PARALLEL_SEGMENTS", raising=False)
        monkeypatch.setitem(paths._RENDERING, "parallel_segments", "3")
        assert paths.render_parallel_segments() == 3

    def test_bad_env_falls_back_to_config(self, monkeypatch):
        monkeypatch.setitem(paths._RENDERING, "parallel_segments", "2")
        monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "lots")
        assert paths.render_parallel_segments() == 2

    def test_default_in_config_yaml_is_auto(self):
        from manimgen import config

        assert config.load()["rendering"]["parallel_segments"] == "auto"

    def test_config_rejects_nonsense(self, tmp_path, monkeypatch):
        from manimgen import config

        text = Path(config.config_path()).read_text(encoding="utf-8")
        bad = text.replace("parallel_segments: auto", "parallel_segments: 0")
        assert bad != text
        f = tmp_path / "config.yaml"
        f.write_text(bad, encoding="utf-8")
        monkeypatch.setenv("MANIMGEN_CONFIG", str(f))
        config.reload()
        try:
            with pytest.raises(config.ConfigError):
                config.load()
        finally:
            monkeypatch.delenv("MANIMGEN_CONFIG")
            config.reload()


# ---------------------------------------------------------------------------
# Unit: merging probe reports
# ---------------------------------------------------------------------------


def _finding(time_, a, b="", kind="overlap", fraction=0.5):
    return {
        "time": time_,
        "a": a,
        "b": b,
        "fraction": fraction,
        "a_kind": "Text",
        "b_kind": "Text",
        "kind": kind,
    }


@pytest.mark.unit
class TestMergeReports:
    def test_concatenates_in_order_and_sums_counters(self):
        reports = [
            {"findings": [_finding(1.0, "a", "b")], "checks": 3, "nudges": 1},
            {"findings": [_finding(4.0, "c", "d")], "checks": 4, "nudges": 0},
            {"findings": [_finding(9.0, "e", "", "offscreen")], "checks": 2},
        ]
        merged = sr.merge_reports(reports, "S")
        assert [f["a"] for f in merged["findings"]] == ["a", "c", "e"]
        assert merged["checks"] == 9
        assert merged["nudges"] == 1
        assert merged["scene"] == "S"
        assert merged["segments"] == 3

    def test_caps_and_duplicates_match_a_single_run(self):
        # the same pair found in two segments is one finding; more than 10 are
        # capped, exactly as the recorder does in one process
        reports = [
            {"findings": [_finding(1.0, "same", "pair")]},
            {"findings": [_finding(5.0, "same", "pair")]},
            {"findings": [_finding(6.0 + i, f"x{i}", f"y{i}") for i in range(12)]},
        ]
        merged = sr.merge_reports(reports)
        assert len(merged["findings"]) == overlap_probe.MAX_FINDINGS
        assert merged["findings"][0]["time"] == 1.0
        assert sum(1 for f in merged["findings"] if f["a"] == "same") == 1

    def test_parses_with_the_host_reader(self, tmp_path):
        merged = sr.merge_reports([{"findings": [_finding(2.0, "a", "b")]}])
        path = tmp_path / "m.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        rep = overlap_report.read_report(path)
        assert len(rep.overlaps) == 1 and rep.probe_error is None

    def test_probe_error_survives(self):
        merged = sr.merge_reports([{}, {"probe_error": "observe: boom"}])
        assert merged["probe_error"] == "observe: boom"


# ---------------------------------------------------------------------------
# Unit: the probe's skipping rules (fakes, no GL)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frames_in_matches_manimgl_progression():
    import numpy as np

    for run_time in (0.45, 1.0, 1.37, 0.01, 2.5):
        assert overlap_probe._frames_in(run_time, 30) == len(
            np.arange(0, run_time, 1 / 30)
        )


class _FakeScene:
    """Just enough of manimlib's Scene for the skipping patch."""

    def __init__(self):
        self.skip_animations = True
        self.time = 0.0
        self.num_plays = 0
        self.updates = []

        class Cam:
            fps = 30

        self.camera = Cam()

    def get_time_progression(
        self, run_time, n_iterations=None, desc="", override_skip_animations=False
    ):
        return ["original", run_time]

    def update_mobjects(self, dt):
        self.updates.append(dt)

    def finish_animations(self, animations):
        raise AssertionError("the original must not run while skipping")


@pytest.mark.unit
def test_skipped_animation_advances_whole_frames_and_updates_once():
    overlap_probe._patch_skipping(_FakeScene)
    scene = _FakeScene()
    assert scene.get_time_progression(1.37) == [pytest.approx(42 / 30)]
    done = []

    class Anim:
        def finish(self):
            done.append("finish")

        def clean_up_from_scene(self, s):
            done.append("clean")

    scene.finish_animations([Anim()])
    assert done == ["finish", "clean"]
    assert scene.updates == [0]  # not run_time a second time
    scene.skip_animations = False
    assert scene.get_time_progression(1.37)[0] == "original"


# ---------------------------------------------------------------------------
# Unit: cancel in procutil
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cancel_kills_the_tree_without_calling_it_a_timeout():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    start = time.monotonic()
    code, _out, _err, timed_out = procutil.run_tree(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        timeout=60,
        cancel=cancel,
    )
    assert time.monotonic() - start < 20
    assert code is None and timed_out is False


# ---------------------------------------------------------------------------
# Unit: fallbacks with a faked process layer
# ---------------------------------------------------------------------------

_LONG_SCENE = """\
from manimlib import *


class LongScene(Scene):
    def construct(self):
        for _ in range(12):
            self.wait(1.0)
"""


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    scene = tmp_path / "long.py"
    scene.write_text(_LONG_SCENE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "2")
    monkeypatch.setenv("MANIMGEN_OVERLAP_PROBE", "1")
    return scene


def _plan_report(env, times):
    Path(env[overlap_report.REPORT_ENV]).write_text(
        json.dumps({"play_times": times, "num_plays": len(times), "findings": []}),
        encoding="utf-8",
    )


def _install_fake(monkeypatch, behaviour):
    calls = []

    def fake(cmd, *, timeout, cwd=None, env=None, cancel=None):
        calls.append(cmd)
        return behaviour(cmd, timeout, env, cancel)

    monkeypatch.setattr(procutil, "run_tree", fake)
    return calls


@pytest.mark.unit
def test_failed_segment_falls_back_to_single_render(fake_env, monkeypatch):
    times = [float(i) for i in range(1, 13)]

    def behaviour(cmd, timeout, env, cancel):
        if "-n" in cmd and cmd[cmd.index("-n") + 1] == str(sr._PLAN_START):
            _plan_report(env, times)
            return 0, "", "", False
        if "-n" in cmd:
            if cmd[cmd.index("-n") + 1].startswith("0,"):
                return 1, "", "Traceback: boom", False
            time.sleep(0.2)
            return None, "", "", False  # cancelled sibling
        raise AssertionError("no join after a failed segment")

    calls = _install_fake(monkeypatch, behaviour)
    result = sr.render(str(fake_env), "LongScene", 300.0)
    assert result is None
    assert len(calls) == 3  # plan and two segments, nothing else
    assert sr.BUDGET.in_use == 0


@pytest.mark.unit
def test_timeout_in_a_segment_is_reported_not_retried(fake_env, monkeypatch):
    times = [float(i) for i in range(1, 13)]

    def behaviour(cmd, timeout, env, cancel):
        if cmd[cmd.index("-n") + 1] == str(sr._PLAN_START):
            _plan_report(env, times)
            return 0, "", "", False
        return None, "partial", "", True

    _install_fake(monkeypatch, behaviour)
    result = sr.render(str(fake_env), "LongScene", 300.0)
    assert result is not None and result.ok is False and result.timed_out is True
    assert "TimeoutExpired" in result.stderr


@pytest.mark.unit
def test_failed_plan_renders_normally(fake_env, monkeypatch):
    calls = _install_fake(monkeypatch, lambda *a: (1, "", "SyntaxError", False))
    assert sr.render(str(fake_env), "LongScene", 300.0) is None
    assert len(calls) == 1


@pytest.mark.unit
def test_scene_known_to_be_short_is_never_planned(tmp_path, monkeypatch):
    scene = tmp_path / "s.py"
    scene.write_text(
        "from manimlib import *\n\n\nclass S(Scene):\n"
        "    def construct(self):\n        self.wait(2.0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "3")
    calls = _install_fake(monkeypatch, lambda *a: (0, "", "", False))
    assert sr.render(str(scene), "S", 300.0) is None
    assert calls == []


@pytest.mark.unit
def test_one_process_setting_never_plans(fake_env, monkeypatch):
    monkeypatch.setenv("MANIMGEN_PARALLEL_SEGMENTS", "1")
    calls = _install_fake(monkeypatch, lambda *a: (0, "", "", False))
    assert sr.render(str(fake_env), "LongScene", 300.0) is None
    assert calls == []


@pytest.mark.unit
def test_run_manimgl_uses_the_normal_render_when_segmenting_declines(
    fake_env, monkeypatch
):
    monkeypatch.setattr(sr, "render", lambda *a, **k: None)
    seen = []

    def fake(cmd, *, timeout, cwd=None, env=None, cancel=None):
        seen.append(cmd)
        return 1, "", "boom", False

    monkeypatch.setattr(procutil, "run_tree", fake)
    result = render_command.run_manimgl(str(fake_env), "LongScene", timeout=5)
    assert result.ok is False and len(seen) == 1 and "-n" not in seen[0]


@pytest.mark.unit
def test_run_manimgl_returns_the_segmented_result(fake_env, monkeypatch):
    sentinel = render_command.RenderResult(True, "videos/x.mp4", "", "", 0, False)
    monkeypatch.setattr(sr, "render", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        procutil, "run_tree", lambda *a, **k: pytest.fail("single render ran")
    )
    assert render_command.run_manimgl(str(fake_env), "LongScene") is sentinel


# ---------------------------------------------------------------------------
# Integration: real renders under xvfb
# ---------------------------------------------------------------------------


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform != "win32"


real = pytest.mark.skipif(
    importlib.util.find_spec("manimlib") is None
    or shutil.which("manimgl") is None
    or shutil.which("ffmpeg") is None
    or shutil.which("ffprobe") is None
    or not _has_display(),
    reason="needs manimgl, ffmpeg and a display (xvfb-run on Linux)",
)

UPDATER_SCENE = """\
from manimlib import *


class UpdaterScene(Scene):
    def construct(self):
        tracker = ValueTracker(0)
        dot = Dot(color=YELLOW).move_to(LEFT * 3)
        dot.add_updater(lambda m, dt: m.shift(0.5 * dt * RIGHT))
        line = always_redraw(lambda: Line(LEFT * 3, dot.get_center(), color=BLUE))
        number = always_redraw(lambda: DecimalNumber(tracker.get_value()).to_edge(UP))
        note = Text("note").to_edge(DOWN)
        word = Text("hello").shift(DOWN)
        self.add(dot, line, number)
        self.play(FadeIn(word), run_time=0.7)
        self.play(tracker.animate.set_value(5), run_time=1.37)
        self.wait(0.53)
        self.play(Write(note), run_time=0.91)
        self.play(tracker.animate.set_value(2), dot.animate.set_color(RED), run_time=1.12)
        self.wait(0.8)
        self.play(FadeOut(word), run_time=0.45)
        self.play(tracker.animate.set_value(9), run_time=1.0)
        self.wait(1.21)
"""

FINDINGS_SCENE = """\
from manimlib import *


class FindingsScene(Scene):
    def construct(self):
        a = Text("first label")
        b = Text("first label two").shift(0.05 * DOWN)
        self.play(FadeIn(a), run_time=1.0)
        self.play(FadeIn(b), run_time=1.0)
        self.wait(1.0)
        self.play(FadeOut(a), FadeOut(b), run_time=1.0)
        c = Text("second one").shift(UP)
        d = Text("second two").shift(UP + 0.02 * RIGHT)
        self.play(FadeIn(c), FadeIn(d), run_time=1.5)
        self.wait(1.0)
        self.play(FadeOut(c), FadeOut(d), run_time=0.5)
        e = Text("far away off screen text here", font_size=48).shift(RIGHT * 6.5)
        self.play(FadeIn(e), run_time=1.0)
        self.wait(1.0)
"""

BENCH_SCENE = """\
from manimlib import *


class BenchScene(Scene):
    def construct(self):
        axes = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], width=7, height=4.5)
        axes.shift(DOWN * 0.5)
        tracker = ValueTracker(-2)
        graph = axes.get_graph(lambda x: x**2, color=BLUE)
        dot = always_redraw(
            lambda: Dot(axes.i2gp(tracker.get_value(), graph), color=YELLOW)
        )
        title = Text("Parabola", font_size=36).to_edge(UP)
        spinner = Square(color=RED).scale(0.4).to_corner(UR)
        spinner.add_updater(lambda m, dt: m.rotate(dt))
        self.add(spinner)
        self.play(ShowCreation(axes), run_time=2.3)
        self.play(ShowCreation(graph), Write(title), run_time=3.1)
        self.add(dot)
        for target in [2, -1, 1.5, -2.5, 0, 2.5]:
            label = Text(f"x = {target}", font_size=28).next_to(axes, RIGHT)
            self.play(tracker.animate.set_value(target), FadeIn(label), run_time=2.2)
            self.wait(0.7)
            self.play(FadeOut(label), run_time=0.4)
        self.wait(2.1)
        self.play(graph.animate.set_color(GREEN), run_time=1.9)
        self.play(FadeOut(VGroup(axes, graph, dot)), run_time=1.3)
        self.wait(1.4)
"""


def _render(tmp_path, name, code, cls, segments, *, short=False):
    """Render through run_manimgl at draft size; return (result, video, sidecar)."""
    folder = tmp_path / f"{name}_{segments}"
    folder.mkdir()
    scene = folder / f"{name}.py"
    scene.write_text(code, encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(folder)
    old = os.environ.get("MANIMGEN_PARALLEL_SEGMENTS")
    os.environ["MANIMGEN_PARALLEL_SEGMENTS"] = str(segments)
    try:
        if short:
            paths.set_video_format("short")
        started = time.monotonic()
        res = render_command.run_manimgl(str(scene), cls, timeout=900, draft=True)
        elapsed = time.monotonic() - started
    finally:
        os.chdir(cwd)
        if short:
            paths.set_video_format("landscape")
        if old is None:
            os.environ.pop("MANIMGEN_PARALLEL_SEGMENTS", None)
        else:
            os.environ["MANIMGEN_PARALLEL_SEGMENTS"] = old
    assert res.ok, res.stderr[-800:]
    video = (folder / res.video_path).resolve()
    sidecar = {}
    side = Path(str(video) + ".overlaps.json")
    if side.exists():
        sidecar = json.loads(side.read_text(encoding="utf-8"))
    return res, video, sidecar, elapsed


def _frames(video) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out.strip().rstrip(","))


def _duration(video) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return float(out.strip())


def _psnr_series(a, b) -> list[float]:
    """PSNR of each frame pair (inf for identical frames)."""
    log = Path(str(a) + ".psnr.log")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(a),
            "-i",
            str(b),
            "-lavfi",
            f"psnr=stats_file={log.name}",
            "-f",
            "null",
            "-",
        ],
        cwd=str(log.parent),
        check=True,
        capture_output=True,
    )
    values = []
    for line in log.read_text().splitlines():
        for part in line.split():
            if part.startswith("psnr_avg:"):
                raw = part.split(":", 1)[1]
                values.append(float("inf") if raw == "inf" else float(raw))
    return values


@pytest.fixture
def force_segments(monkeypatch):
    """Let short test scenes be cut into small pieces."""
    monkeypatch.setattr(sr, "MIN_VIDEO_SECONDS", 2.0)
    monkeypatch.setattr(sr, "MIN_SEGMENT_SECONDS", 1.0)
    monkeypatch.setattr(sr, "MAX_SEGMENT_SHARE", 0.9)


def _segments_in_log(caplog) -> int | None:
    for rec in caplog.records:
        msg = rec.getMessage()
        if "segments joined" in msg:
            return int(msg.split("]")[1].split(":")[1].split()[0])
    return None


@pytest.mark.integration
@pytest.mark.timeout(900)
@real
class TestRealRenders:
    def test_updater_scene_is_frame_for_frame_the_same_length(
        self, tmp_path, force_segments, caplog
    ):
        caplog.set_level("INFO")
        _, single, _, _ = _render(tmp_path, "upd", UPDATER_SCENE, "UpdaterScene", 1)
        _, split, _, _ = _render(tmp_path, "upd", UPDATER_SCENE, "UpdaterScene", 3)
        assert _segments_in_log(caplog) in (2, 3), "the scene was not segmented"
        assert _frames(split) == _frames(single) == 246
        assert _duration(split) == pytest.approx(_duration(single), abs=1e-3)
        series = _psnr_series(split, single)
        assert len(series) == 246
        # the encoder is lossy, so frames differ by noise; a wrong frame (a
        # moved dot, a missing fade step) would sit far below this
        assert min(series) > 45.0

    def test_join_frames_match(self, tmp_path, force_segments, caplog):
        """Pixels around every join: first and last frame of each piece."""
        caplog.set_level("INFO")
        _, single, _, _ = _render(tmp_path, "upd2", UPDATER_SCENE, "UpdaterScene", 1)
        _, split, _, _ = _render(tmp_path, "upd2", UPDATER_SCENE, "UpdaterScene", 3)
        series = _psnr_series(split, single)
        times = [0.7, 2.1, 2.6333, 3.5667, 4.7, 5.5, 5.9667, 6.9667, 8.2]
        ranges = sr.split_plays(times, _segments_in_log(caplog) or 3)
        joins = [round(times[s - 1] * 30) for s, _ in ranges[1:]]
        assert joins
        for frame in joins:
            for k in range(frame - 2, frame + 3):
                assert series[k] > 45.0, f"frame {k} next to the join at {frame}"

    def test_pixels_are_identical_with_a_frame_independent_codec(
        self, tmp_path, force_segments
    ):
        """Isolates the render from the H.264 noise: ProRes (``-t``) frames are
        encoded one by one, so segments joined with a stream copy must hash
        exactly like one full render, updaters and always_redraw included."""
        folder = tmp_path / "prores"
        folder.mkdir()
        scene = folder / "u.py"
        scene.write_text(UPDATER_SCENE, encoding="utf-8")
        env = sr._child_env(str(folder / "r.json"), passive=True)

        def run(label, start=None, end=None):
            out = folder / label
            if start is None:
                cmd = [
                    *render_command.build_manimgl_command(
                        str(scene), "UpdaterScene", True
                    ),
                    "-t",
                    "--video_dir",
                    str(out),
                ]
            else:
                cmd = [
                    *sr.segment_command(
                        str(scene), "UpdaterScene", True, start, end, str(out)
                    ),
                    "-t",
                ]
            return subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

        procs = [run("full"), run("a", 0, 3), run("b", 3, 6), run("c", 6, None)]
        for p in procs:
            assert p.wait(timeout=800) == 0
        pieces = [
            folder / "a" / "UpdaterScene_0_3.mov",
            folder / "b" / "UpdaterScene_3_6.mov",
            folder / "c" / "UpdaterScene_6.mov",
        ]
        listing = folder / "join.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces))
        joined = folder / "joined.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(joined),
            ],
            check=True,
        )

        def hashes(path):
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            return [
                ln.split(",")[-1].strip()
                for ln in out.splitlines()
                if not ln.startswith("#")
            ]

        full = hashes(folder / "full" / "UpdaterScene.mov")
        assert len(full) == 246
        assert hashes(joined) == full

    def test_merged_probe_report_equals_the_single_render_report(
        self, tmp_path, force_segments, caplog
    ):
        caplog.set_level("INFO")
        res1, _, single, _ = _render(
            tmp_path, "find", FINDINGS_SCENE, "FindingsScene", 1
        )
        res3, _, split, _ = _render(
            tmp_path, "find", FINDINGS_SCENE, "FindingsScene", 3
        )
        assert _segments_in_log(caplog) in (2, 3)
        assert single["findings"], "the scene must have findings to compare"
        assert split["findings"] == single["findings"]
        assert split["checks"] == single["checks"]
        assert split["dropped"] == single["dropped"]
        assert split["num_plays"] == single["num_plays"]
        assert res3.overlaps == res1.overlaps
        kinds = {f["kind"] for f in single["findings"]}
        assert {"overlap", "offscreen"} <= kinds

    def test_kit_scenes_keep_their_length(self, tmp_path, force_segments, caplog):
        from tests.kit_scenes import SCENES

        caplog.set_level("INFO")
        for name in sorted(SCENES):
            _, single, _, _ = _render(tmp_path, name, SCENES[name], name, 1, short=True)
            caplog.clear()
            _, split, _, _ = _render(tmp_path, name, SCENES[name], name, 2, short=True)
            assert _segments_in_log(caplog) == 2, f"{name} was not segmented"
            assert _frames(split) == _frames(single), name
            assert min(_psnr_series(split, single)) > 45.0, name

    def test_speedup_on_a_long_scene(self, tmp_path, caplog):
        if sr.core_budget() < 3:
            pytest.skip("needs at least 4 cores for a speedup")
        caplog.set_level("INFO")
        _, single, _, t_single = _render(
            tmp_path, "bench", BENCH_SCENE, "BenchScene", 1
        )
        _, split, _, t_split = _render(tmp_path, "bench", BENCH_SCENE, "BenchScene", 3)
        assert _segments_in_log(caplog) == 3
        assert _frames(split) == _frames(single) == 957
        print(
            f"single {t_single:.1f}s, segmented {t_split:.1f}s, x{t_single / t_split:.2f}"
        )
        # other work on the machine blurs the ratio; it must still win
        assert t_split < t_single
