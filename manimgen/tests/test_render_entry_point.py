"""One render entry point (#80 R15).

runner, retry and fallback used to each run manimgl with their own
``subprocess.run`` (timeout kills only the direct child, fixed budgets, and an
exit-0 render with no fresh video reported as success with ``None``). They now
share ``render_command.run_manimgl`` on top of ``procutil.run_tree``.
"""

import os
import subprocess
import sys
import time

import pytest

from manimgen import paths, procutil
from manimgen.validator import fallback as fallback_mod
from manimgen.validator import render_command
from manimgen.validator import retry as retry_mod
from manimgen.validator import runner as runner_mod

_SCENE = "from manimlib import *\n\n\nclass S(Scene):\n    pass\n"
_OK_PRECHECK = {"ok": True, "applied_fixes": [], "layout_warnings": [], "stderr": ""}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    p = tmp_path / "section_01.py"
    p.write_text(_SCENE, encoding="utf-8")
    monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(paths, "scenes_dir", lambda: str(tmp_path))
    for mod in (runner_mod, retry_mod):
        monkeypatch.setattr(mod, "precheck_and_autofix_file", lambda p: _OK_PRECHECK)
    return str(p)


def _leaf(monkeypatch, result):
    """Replace the process leaf; returns the list of recorded calls."""
    calls: list = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return result

    monkeypatch.setattr(procutil, "run_tree", fake)
    return calls


def _no_video(monkeypatch):
    monkeypatch.setattr(runner_mod, "_find_rendered_video", lambda *a, **k: None)


# -- exit 0 without a fresh video is a failure --------------------------------


def test_runner_exit_zero_without_video_is_failure(scene, monkeypatch):
    _leaf(monkeypatch, (0, "", "", False))
    _no_video(monkeypatch)
    ok, video = runner_mod.run_scene(scene, "SceneA")
    assert ok is False and video is None


def test_retry_capture_exit_zero_without_video_is_failure(scene, monkeypatch):
    _leaf(monkeypatch, (0, "", "", False))
    _no_video(monkeypatch)
    res = retry_mod._run_and_capture(scene, "SceneA")
    assert res["success"] is False and res["video_path"] is None
    assert "no fresh video" in res["stderr"]


def test_retry_scene_does_not_raise_on_exit_zero_without_video(
    scene, monkeypatch, tmp_path
):
    """Before the fix check_frames(None) raised TypeError and ended the run."""
    _leaf(monkeypatch, (0, "", "", False))
    _no_video(monkeypatch)
    monkeypatch.setattr(retry_mod, "_load_retry_system_prompt", lambda: "sys")
    monkeypatch.setattr(retry_mod, "apply_error_aware_fixes", lambda c, e: (c, []))
    monkeypatch.setattr(retry_mod, "_write_attempt_artifacts", lambda *a: None)
    monkeypatch.setattr(retry_mod, "chat", lambda **kw: _SCENE + "# fixed\n")
    ok, video = retry_mod.retry_scene(
        {"id": "s1", "title": "T"}, _SCENE, "SceneA", scene
    )
    assert ok is False and video is None


def test_fallback_exit_zero_without_video_returns_none(scene, monkeypatch):
    _leaf(monkeypatch, (0, "", "", False))
    _no_video(monkeypatch)
    out = fallback_mod.fallback_scene({"id": "section_01", "title": "T"})
    assert out is None


def test_failure_message_lists_searched_folders(scene, monkeypatch):
    _leaf(monkeypatch, (0, "", "", False))
    _no_video(monkeypatch)
    res = render_command.run_manimgl(scene, "SceneA", timeout=5)
    assert not res.ok and res.video_path is None and not res.timed_out
    for d in runner_mod._video_search_dirs():
        assert d in res.stderr


# -- success and timeout paths -------------------------------------------------


def test_success_returns_fresh_video(scene, monkeypatch):
    calls = _leaf(monkeypatch, (0, "out", "", False))
    monkeypatch.setattr(runner_mod, "_find_rendered_video", lambda *a, **k: "/v/a.mp4")
    assert runner_mod.run_scene(scene, "SceneA") == (True, "/v/a.mp4")
    assert calls[0][0][0] == "manimgl"


def test_nonzero_exit_is_failure_with_stderr(scene, monkeypatch):
    _leaf(monkeypatch, (1, "", "boom", False))
    find = []
    monkeypatch.setattr(
        runner_mod, "_find_rendered_video", lambda *a, **k: find.append(1)
    )
    res = retry_mod._run_and_capture(scene, "SceneA")
    assert res == {"success": False, "video_path": None, "stderr": "boom"}
    assert not find


def test_timeout_is_failure_in_every_caller(scene, monkeypatch):
    _leaf(monkeypatch, (None, "", "", True))
    _no_video(monkeypatch)
    assert runner_mod.run_scene(scene, "SceneA") == (False, None)
    res = retry_mod._run_and_capture(scene, "SceneA")
    assert res["success"] is False and "TimeoutExpired" in res["stderr"]
    assert fallback_mod.fallback_scene({"id": "section_01", "title": "T"}) is None


# -- configurable budgets ------------------------------------------------------


def test_default_budgets_are_unchanged():
    for var in ("2D", "3D", "FALLBACK"):
        os.environ.pop(f"MANIMGEN_RENDER_TIMEOUT_{var}", None)
    assert paths.render_timeout("2d") == 240
    assert paths.render_timeout("3d") == 360
    assert paths.render_timeout("fallback") == 180


def test_env_override_and_bad_values(monkeypatch):
    monkeypatch.setenv("MANIMGEN_RENDER_TIMEOUT_2D", "900")
    assert paths.render_timeout("2d") == 900
    for bad in ("abc", "0", "-5"):
        monkeypatch.setenv("MANIMGEN_RENDER_TIMEOUT_2D", bad)
        assert paths.render_timeout("2d") == 240


def test_config_value_is_read(monkeypatch):
    monkeypatch.delenv("MANIMGEN_RENDER_TIMEOUT_3D", raising=False)
    monkeypatch.setitem(paths._RENDERING, "render_timeout_3d", 500.0)
    assert paths.render_timeout("3d") == 500


def test_timeout_passed_to_leaf_follows_scene_kind_and_config(tmp_path, monkeypatch):
    for var in ("2D", "3D"):
        monkeypatch.delenv(f"MANIMGEN_RENDER_TIMEOUT_{var}", raising=False)
    two = tmp_path / "a.py"
    two.write_text(_SCENE, encoding="utf-8")
    three = tmp_path / "b.py"
    three.write_text(_SCENE.replace("(Scene)", "(ThreeDScene)"), encoding="utf-8")
    calls = _leaf(monkeypatch, (1, "", "x", False))
    render_command.run_manimgl(str(two), "A")
    render_command.run_manimgl(str(three), "B")
    render_command.run_manimgl(str(two), "A", timeout=7)
    assert [c[1]["timeout"] for c in calls] == [240, 360, 7]
    monkeypatch.setenv("MANIMGEN_RENDER_TIMEOUT_2D", "11")
    render_command.run_manimgl(str(two), "A")
    assert calls[-1][1]["timeout"] == 11


def test_fallback_uses_fallback_budget(scene, monkeypatch):
    monkeypatch.delenv("MANIMGEN_RENDER_TIMEOUT_FALLBACK", raising=False)
    calls = _leaf(monkeypatch, (1, "", "x", False))
    fallback_mod.fallback_scene({"id": "section_01", "title": "T"})
    assert calls[0][1]["timeout"] == 180


def test_callers_do_not_run_subprocesses_themselves():
    for mod in (runner_mod, retry_mod, fallback_mod):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "subprocess.run(" not in src and "subprocess.Popen(" not in src


# -- real process tree ---------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class TestRunTreeReal:
    def test_returns_output(self):
        code = "import sys; print('hi'); print('err', file=sys.stderr)"
        rc, out, err, timed_out = procutil.run_tree(
            [sys.executable, "-c", code], timeout=30
        )
        assert rc == 0 and "hi" in out and "err" in err and not timed_out

    def test_unstartable_command_does_not_raise(self):
        rc, _, err, timed_out = procutil.run_tree(
            ["definitely-not-a-real-binary-xyz"], timeout=5
        )
        assert rc is None and not timed_out and "could not start" in err

    def test_timeout_kills_grandchild(self, tmp_path):
        pid_file = tmp_path / "grandchild.pid"
        code = (
            "import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(c.pid))\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        rc, _, _, timed_out = procutil.run_tree([sys.executable, "-c", code], timeout=2)
        assert timed_out and rc is None
        assert time.monotonic() - started < 25
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        assert not _pid_alive(pid), "grandchild survived the timeout"
