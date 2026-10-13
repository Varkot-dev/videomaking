"""Unit tests for scripts/env_doctor.py (headless display and unreadable-file handling).

The script lives in scripts/ (not a package), so it is loaded by path. Every
subprocess, env var and file read is mocked: nothing here imports manimlib.
"""

from __future__ import annotations

import builtins
import importlib.util
import os
import subprocess
import sys

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "env_doctor.py",
)
_spec = importlib.util.spec_from_file_location("env_doctor", _SCRIPT)
ed = importlib.util.module_from_spec(_spec)
sys.modules["env_doctor"] = ed
_spec.loader.exec_module(ed)

DISPLAY_TRACEBACK = (
    b"Traceback (most recent call last):\n"
    b'  File "<string>", line 1, in <module>\n'
    b'pyglet.display.xlib.NoSuchDisplayException: Cannot connect to "None"\n'
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    ed._blocking_failures.clear()
    ed._warnings.clear()
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())


def _failing_import(stderr: bytes):
    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=stderr)

    return run


class TestHeadlessDetection:
    def test_linux_without_display_is_headless(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert ed._is_headless_linux()

    @pytest.mark.parametrize("var", ["DISPLAY", "WAYLAND_DISPLAY"])
    def test_linux_with_a_display_is_not_headless(self, monkeypatch, var):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv(var, ":0")
        assert not ed._is_headless_linux()

    @pytest.mark.parametrize("plat", ["win32", "darwin"])
    def test_other_platforms_never_headless(self, monkeypatch, plat):
        monkeypatch.setattr(sys, "platform", plat)
        monkeypatch.delenv("DISPLAY", raising=False)
        assert not ed._is_headless_linux()


class TestManimlibImportOnHeadlessBox:
    def _setup(self, monkeypatch, xvfb):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(subprocess, "run", _failing_import(DISPLAY_TRACEBACK))
        monkeypatch.setattr(
            ed.shutil, "which", lambda name: "/usr/bin/xvfb-run" if xvfb else None
        )

    def test_says_to_use_xvfb_run(self, monkeypatch, capsys):
        self._setup(monkeypatch, xvfb=True)
        ed.check_manimlib_imports()
        err = capsys.readouterr().err
        assert "xvfb-run -a" in err
        assert "investigate the traceback" not in err
        assert not ed._blocking_failures
        assert ed._warnings == ["manimlib import (no display)"]

    def test_missing_xvfb_run_is_blocking_and_names_the_package(
        self, monkeypatch, capsys
    ):
        self._setup(monkeypatch, xvfb=False)
        ed.check_manimlib_imports()
        err = capsys.readouterr().err
        assert "apt-get install xvfb" in err and "xvfb-run -a" in err
        assert ed._blocking_failures == ["manimlib import (no display)"]

    def test_display_error_with_a_display_set_is_still_investigated(
        self, monkeypatch, capsys
    ):
        self._setup(monkeypatch, xvfb=True)
        monkeypatch.setenv("DISPLAY", ":99")
        ed.check_manimlib_imports()
        assert ed._blocking_failures == ["manimlib import"]
        assert "investigate the traceback" in capsys.readouterr().err

    def test_other_import_errors_keep_the_old_message(self, monkeypatch):
        self._setup(monkeypatch, xvfb=True)
        monkeypatch.setattr(
            subprocess, "run", _failing_import(b"ImportError: something else\n")
        )
        ed.check_manimlib_imports()
        assert ed._blocking_failures == ["manimlib import"]


class TestFpsScanSurfacesUnreadableFiles:
    def _tree(self, tmp_path, monkeypatch):
        vdir = tmp_path / "manimgen" / "validator"
        vdir.mkdir(parents=True)
        (vdir / "ok.py").write_text("x = 1\n", encoding="utf-8")
        (vdir / "locked.py").write_text("x = 2\n", encoding="utf-8")
        monkeypatch.setattr(ed, "PROJECT_ROOT", str(tmp_path))
        return vdir

    def test_unreadable_file_warns_instead_of_reporting_clean(
        self, tmp_path, monkeypatch, capsys
    ):
        self._tree(tmp_path, monkeypatch)
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if str(path).endswith("locked.py"):
                raise PermissionError("denied")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, "open", fake_open)
        ed.check_no_broken_fps_flag()
        err = capsys.readouterr().err
        assert "locked.py" in err
        assert "no broken --fps flag" not in err
        assert ed._warnings == ["--fps scan incomplete"]
        assert not ed._blocking_failures

    def test_undecodable_file_is_still_scanned(self, tmp_path, monkeypatch):
        vdir = self._tree(tmp_path, monkeypatch)
        (vdir / "locked.py").write_bytes(b'\xff\xfe cmd = ["--fps"]\n')
        ed.check_no_broken_fps_flag()
        assert ed._blocking_failures == ["broken --fps flag"]

    def test_clean_tree_passes(self, tmp_path, monkeypatch, capsys):
        self._tree(tmp_path, monkeypatch)
        ed.check_no_broken_fps_flag()
        assert "no broken --fps flag" in capsys.readouterr().err
        assert not ed._warnings and not ed._blocking_failures
