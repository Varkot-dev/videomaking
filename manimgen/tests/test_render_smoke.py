"""Unit tests for scripts/render_smoke.py (pure logic only).

No real renders, no network, no real GL: every tool lookup and subprocess is
mocked. The script lives in scripts/ (not a package), so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "render_smoke.py",
)
_spec = importlib.util.spec_from_file_location("render_smoke", _SCRIPT)
rs = importlib.util.module_from_spec(_spec)
sys.modules["render_smoke"] = rs
_spec.loader.exec_module(rs)

FFPROBE_OK = json.dumps(
    {
        "streams": [
            {"codec_type": "audio"},
            {
                "codec_type": "video",
                "width": 854,
                "height": 480,
                "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
            },
        ],
        "format": {"duration": "2.533"},
    }
)


class TestParseFfprobe:
    def test_extracts_video_fields(self):
        info = rs.parse_ffprobe_json(FFPROBE_OK)
        assert info["has_video"] is True
        assert (info["width"], info["height"]) == (854, 480)
        assert info["fps"] == 30.0
        assert info["duration"] == pytest.approx(2.533)
        assert info["has_audio"] is True

    def test_fractional_rate(self):
        assert rs.parse_fraction("30000/1001") == pytest.approx(29.97, abs=0.01)

    @pytest.mark.parametrize("bad", [None, "", "0/0", "abc", "1/x"])
    def test_bad_fraction_is_none(self, bad):
        assert rs.parse_fraction(bad) is None

    def test_no_video_stream(self):
        raw = json.dumps({"streams": [{"codec_type": "audio"}], "format": {}})
        info = rs.parse_ffprobe_json(raw)
        assert info["has_video"] is False
        assert info["duration"] is None

    def test_duration_falls_back_to_stream(self):
        raw = json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1,
                        "height": 1,
                        "r_frame_rate": "15/1",
                        "duration": "2.0",
                    }
                ]
            }
        )
        info = rs.parse_ffprobe_json(raw)
        assert info["duration"] == 2.0
        assert info["fps"] == 15.0

    @pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", None])
    def test_invalid_json_raises_value_error(self, raw):
        with pytest.raises(ValueError):
            rs.parse_ffprobe_json(raw)


class TestEvaluateProbe:
    def test_pass(self):
        status, detail = rs.evaluate_probe(rs.parse_ffprobe_json(FFPROBE_OK), 480)
        assert status == rs.PASS
        assert "854x480" in detail

    def test_no_video_fails(self):
        assert rs.evaluate_probe({"has_video": False}, 480)[0] == rs.FAIL

    def test_too_short_fails(self):
        info = rs.parse_ffprobe_json(FFPROBE_OK)
        info["duration"] = 0.2
        assert rs.evaluate_probe(info, 480)[0] == rs.FAIL

    def test_unknown_duration_fails(self):
        info = rs.parse_ffprobe_json(FFPROBE_OK)
        info["duration"] = None
        assert rs.evaluate_probe(info, 480)[0] == rs.FAIL

    def test_wrong_height_warns(self):
        assert rs.evaluate_probe(rs.parse_ffprobe_json(FFPROBE_OK), 1080)[0] == rs.WARN


class TestSwapQualityFlag:
    def test_replaces_existing_flag(self):
        cmd = ["manimgl", "s.py", "C", "-w", "--hd", "-c", "#1C1C1C"]
        assert rs.swap_quality_flag(cmd, "--hd", "-l") == [
            "manimgl",
            "s.py",
            "C",
            "-w",
            "-l",
            "-c",
            "#1C1C1C",
        ]
        assert "--hd" in cmd  # input not mutated

    def test_inserts_when_missing(self):
        out = rs.swap_quality_flag(["manimgl", "s.py", "C", "-w"], "--hd", "-l")
        assert out[-1] == "-l"


class TestEvaluateGl:
    def _ok(
        self, version="4.6 (Core Profile) Mesa", renderer="Intel(R) UHD Graphics 770"
    ):
        return {"ok": True, "version": version, "renderer": renderer}

    def test_hardware_pass(self):
        c = rs.evaluate_gl(self._ok(), "win32")
        assert c.status == rs.PASS
        assert "UHD Graphics 770" in c.detail

    def test_llvmpipe_warns(self):
        c = rs.evaluate_gl(self._ok(renderer="llvmpipe (LLVM 15, 256 bits)"), "linux")
        assert c.status == rs.WARN
        assert "software" in c.detail

    def test_old_version_fails(self):
        assert rs.evaluate_gl(self._ok(version="2.1 Metal"), "darwin").status == rs.FAIL

    def test_context_failure_explained_with_windows_hint(self):
        c = rs.evaluate_gl({"ok": False, "errors": ["default backend: boom"]}, "win32")
        assert c.status == rs.FAIL
        assert "boom" in c.detail
        assert "opengl32.dll" in c.hint

    def test_missing_payload_is_fail_not_crash(self):
        c = rs.evaluate_gl(None, "linux")
        assert c.status == rs.FAIL
        assert "xvfb" in c.hint

    def test_parse_gl_version(self):
        assert rs.parse_gl_version("4.5 (Core Profile) Mesa 24") == (4, 5)
        assert rs.parse_gl_version("garbage") is None


class TestSummaryAndExit:
    def _checks(self, *statuses):
        return [rs.Check(f"c{i}", s, "d") for i, s in enumerate(statuses)]

    def test_exit_zero_with_warn_and_skip(self):
        assert rs.exit_code(self._checks(rs.PASS, rs.WARN, rs.SKIP)) == 0

    def test_exit_one_on_any_fail(self):
        assert rs.exit_code(self._checks(rs.PASS, rs.FAIL, rs.WARN)) == 1

    def test_empty_is_zero(self):
        assert rs.exit_code([]) == 0

    def test_summary_counts_and_ascii(self):
        text = rs.format_summary(self._checks(rs.PASS, rs.WARN, rs.FAIL, rs.SKIP))
        assert "1 passed, 1 warnings, 1 failed, 1 skipped" in text
        assert "NOT ready" in text
        text.encode("ascii")

    def test_summary_success_verdict(self):
        assert "can render" in rs.format_summary(self._checks(rs.PASS))

    def test_format_check_shows_hint_only_on_problems(self):
        assert "fix:" in rs.format_check(rs.Check("x", rs.FAIL, "bad", "do this"))
        assert "fix:" not in rs.format_check(rs.Check("x", rs.PASS, "ok", "do this"))


class TestMissingTools:
    def test_ffmpeg_missing_is_fail_with_hint(self):
        checks = rs.check_ffmpeg_tools(which=lambda _name: None)
        assert [c.status for c in checks] == [rs.FAIL, rs.FAIL]
        assert all(c.hint for c in checks)

    def test_ffmpeg_present_reports_version(self, monkeypatch):
        monkeypatch.setattr(
            rs,
            "run_tree",
            lambda cmd, **kw: (0, "ffmpeg version 7.1 x\nmore", "", False),
        )
        checks = rs.check_ffmpeg_tools(which=lambda n: f"/bin/{n}")
        assert all(c.status == rs.PASS for c in checks)
        assert "ffmpeg version 7.1" in checks[0].detail

    def test_ffmpeg_unrunnable_is_fail(self, monkeypatch):
        monkeypatch.setattr(rs, "run_tree", lambda cmd, **kw: (1, "", "boom", False))
        checks = rs.check_ffmpeg_tools(which=lambda n: f"/bin/{n}")
        assert all(c.status == rs.FAIL for c in checks)

    def test_run_tree_missing_executable_returns_message(self):
        rc, _out, err, timed_out = rs.run_tree(
            ["definitely-not-a-real-binary-xyz"], timeout=5
        )
        assert rc is None and not timed_out
        assert "could not start" in err

    def test_manimlib_missing_is_readable_fail(self, monkeypatch):
        monkeypatch.setattr(
            rs,
            "run_tree",
            lambda cmd, **kw: (
                1,
                "",
                "ModuleNotFoundError: No module named 'manimlib'",
                False,
            ),
        )
        checks = rs.check_manimlib()
        first = checks[0]
        assert first.status == rs.FAIL
        assert "pip install" in first.hint
        assert "Traceback" not in first.detail.split("\n")[0]

    def test_manimlib_pkg_resources_hint(self, monkeypatch):
        monkeypatch.setattr(
            rs,
            "run_tree",
            lambda cmd, **kw: (
                1,
                "",
                "ModuleNotFoundError: No module named 'pkg_resources'",
                False,
            ),
        )
        assert "setuptools<81" in rs.check_manimlib()[0].hint

    def test_opengl_probe_crash_is_fail(self, monkeypatch):
        monkeypatch.setattr(rs, "run_tree", lambda cmd, **kw: (-11, "", "", False))
        assert rs.check_opengl().status == rs.FAIL

    def test_opengl_probe_parses_last_json_line(self, monkeypatch):
        payload = {"ok": True, "version": "4.5 Mesa", "renderer": "llvmpipe"}
        monkeypatch.setattr(
            rs,
            "run_tree",
            lambda cmd, **kw: (0, "noise\n" + json.dumps(payload), "", False),
        )
        assert rs.check_opengl().status == rs.WARN

    def test_latex_missing_is_warn_not_fail(self, monkeypatch):
        monkeypatch.setattr(rs.shutil, "which", lambda *a, **k: None)
        checks = rs.check_latex()
        assert {c.status for c in checks} == {rs.WARN}


class TestPythonCheck:
    def test_old_python_fails(self):
        checks = rs.check_python(version_info=(3, 9, 1), in_venv=True)
        assert checks[0].status == rs.FAIL

    def test_new_python_passes_and_venv_warn(self):
        checks = rs.check_python(version_info=(3, 11, 4), in_venv=False)
        assert checks[0].status == rs.PASS
        assert checks[1].status == rs.WARN

    def test_venv_pass(self):
        assert (
            rs.check_python(version_info=(3, 13, 0), in_venv=True)[1].status == rs.PASS
        )


class TestRunAll:
    def _args(self, **kw):
        base = {"quick": True, "out": None, "timeout": 5.0, "keep": False}
        base.update(kw)
        return SimpleNamespace(**base)

    def test_blockers_skip_render_and_exit_nonzero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rs, "check_python", lambda: [rs.Check("Python", rs.PASS)])
        monkeypatch.setattr(
            rs, "check_ffmpeg_tools", lambda: [rs.Check("ffmpeg", rs.FAIL, "x", "y")]
        )
        monkeypatch.setattr(
            rs, "check_manimlib", lambda: [rs.Check("manimlib", rs.PASS)]
        )
        monkeypatch.setattr(rs, "check_opengl", lambda: rs.Check("OpenGL", rs.PASS))
        monkeypatch.setattr(rs, "check_latex", lambda: [rs.Check("latex", rs.WARN)])
        called = []
        monkeypatch.setattr(rs, "render_step", lambda *a, **k: called.append(1))
        checks, work = rs.run_all(
            self._args(out=str(tmp_path / "o")), emit=lambda *_: None
        )
        assert not called
        assert rs.exit_code(checks) == 1
        assert any(c.name == "Render 480p" and c.status == rs.SKIP for c in checks)

    def test_quick_skips_default_render(self, monkeypatch, tmp_path):
        for name in (
            "check_python",
            "check_ffmpeg_tools",
            "check_manimlib",
            "check_latex",
        ):
            monkeypatch.setattr(rs, name, lambda: [rs.Check("ok", rs.PASS)])
        monkeypatch.setattr(rs, "check_opengl", lambda: rs.Check("OpenGL", rs.PASS))
        labels = []

        def fake_render(label, **kw):
            labels.append(label)
            return rs.Check(f"Render {label}", rs.PASS, "fine"), 1.0

        monkeypatch.setattr(rs, "render_step", fake_render)
        checks, _ = rs.run_all(
            self._args(out=str(tmp_path / "o")), emit=lambda *_: None
        )
        assert labels == ["480p"]
        assert rs.exit_code(checks) == 0

    def test_full_run_renders_default_too(self, monkeypatch, tmp_path):
        for name in (
            "check_python",
            "check_ffmpeg_tools",
            "check_manimlib",
            "check_latex",
        ):
            monkeypatch.setattr(rs, name, lambda: [rs.Check("ok", rs.PASS)])
        monkeypatch.setattr(rs, "check_opengl", lambda: rs.Check("OpenGL", rs.PASS))
        labels = []

        def fake_render(label, **kw):
            labels.append(label)
            return rs.Check(f"Render {label}", rs.PASS, "fine"), 1.0

        monkeypatch.setattr(rs, "render_step", fake_render)
        rs.run_all(
            self._args(quick=False, out=str(tmp_path / "o")), emit=lambda *_: None
        )
        assert labels == ["480p", "pipeline default"]


class TestMainCleanup:
    def test_temp_dir_removed_and_out_dir_kept(self, monkeypatch, tmp_path):
        made = tmp_path / "work"

        def fake_run_all(args, emit):
            made.mkdir()
            return [rs.Check("a", rs.PASS)], str(made)

        monkeypatch.setattr(rs, "run_all", fake_run_all)
        assert rs.main(["--quick"]) == 0
        assert not made.exists()

        monkeypatch.setattr(rs, "run_all", fake_run_all)
        assert rs.main(["--quick", "--keep"]) == 0
        assert (made / "smoke_report.txt").is_file()
