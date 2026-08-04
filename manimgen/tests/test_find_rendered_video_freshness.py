"""Regression tests for B2 — `_find_rendered_video` returned stale videos.

Two independent defects made a retry validate and ship the *pre-fix* render,
meaning you pay an LLM for a fix that never reached the output:

  1. Substring matching (`class_name in f`) — looking for "Section01Scene"
     also matched "Section01SceneOld.mp4".
  2. No mtime ordering and no freshness floor — os.walk order decided which
     file won, so a video written before the current render started could be
     returned as if it were the render's output.

`cli.py` guards against (2) with a `.hash` sidecar, but `retry.py` and
`fallback.py` call `_find_rendered_video` directly with no guard. The fix
lives inside `_find_rendered_video` so every caller benefits.
"""

import os
import time

import pytest

from manimgen.validator.runner import _find_rendered_video


def _write_video(path: str, mtime: float | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00fakevideo")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def videos_dir(tmp_path, monkeypatch):
    """Point every search dir _find_rendered_video knows about at tmp_path."""
    d = tmp_path / "videos"
    d.mkdir()
    monkeypatch.setattr("manimgen.paths.videos_dir", lambda: str(d))
    # Neutralise the other search roots so the test is hermetic.
    monkeypatch.chdir(tmp_path)
    return d


# ---------------------------------------------------------------------------
# Defect 1 — substring matching picks up a differently-named scene
# ---------------------------------------------------------------------------


def test_exact_stem_wins_over_substring_match(videos_dir):
    """"Section01Scene" must not resolve to "Section01SceneOld.mp4"."""
    now = time.time()
    # The decoy is NEWER, so mtime sorting alone would not save us — only an
    # exact-stem preference does.
    _write_video(str(videos_dir / "Section01SceneOld.mp4"), mtime=now)
    exact = _write_video(str(videos_dir / "Section01Scene.mp4"), mtime=now - 50)

    found = _find_rendered_video("Section01Scene")
    assert found is not None
    assert os.path.samefile(found, exact), (
        f"substring match returned a different scene's video: {found}"
    )


def test_substring_only_match_is_not_returned_as_exact(videos_dir):
    """A file that merely contains the class name is not an exact match.

    It may still be returned as a fallback, but never in preference to a
    genuine exact-stem render.
    """
    now = time.time()
    _write_video(str(videos_dir / "Section01SceneOld.mp4"), mtime=now)
    _write_video(str(videos_dir / "Section01Scene.mp4"), mtime=now)

    found = _find_rendered_video("Section01Scene")
    assert os.path.basename(found) == "Section01Scene.mp4"


# ---------------------------------------------------------------------------
# Defect 2 — no mtime ordering
# ---------------------------------------------------------------------------


def test_newest_render_wins_when_several_match(videos_dir):
    """Among equally-valid matches the most recent render must be returned."""
    now = time.time()
    _write_video(str(videos_dir / "sub_a" / "MyScene.mp4"), mtime=now - 500)
    newest = _write_video(str(videos_dir / "sub_b" / "MyScene.mp4"), mtime=now - 5)
    _write_video(str(videos_dir / "sub_c" / "MyScene.mp4"), mtime=now - 900)

    found = _find_rendered_video("MyScene")
    assert os.path.samefile(found, newest), (
        f"expected newest render, got {found} — results are not mtime-sorted"
    )


# ---------------------------------------------------------------------------
# Defect 3 — no freshness floor: the core "paid for a fix that never shipped" bug
# ---------------------------------------------------------------------------


def test_video_older_than_floor_is_rejected(videos_dir):
    """A render predating the current attempt must NOT be returned.

    This is the bug that makes a retry validate the pre-fix video: the new
    render failed to produce a file, and the stale one from the previous
    attempt got picked up and shipped as if the LLM fix had worked.
    """
    now = time.time()
    _write_video(str(videos_dir / "MyScene.mp4"), mtime=now - 600)

    # The current render started 60s ago — anything older is not its output.
    found = _find_rendered_video("MyScene", newer_than=now - 60)
    assert found is None, (
        f"returned a stale pre-fix render {found} — a retry would ship the "
        f"unfixed video and the paid LLM fix would silently never reach output"
    )


def test_video_newer_than_floor_is_accepted(videos_dir):
    now = time.time()
    fresh = _write_video(str(videos_dir / "MyScene.mp4"), mtime=now - 5)

    found = _find_rendered_video("MyScene", newer_than=now - 60)
    assert found is not None and os.path.samefile(found, fresh)


def test_floor_rejects_stale_but_keeps_fresh_sibling(videos_dir):
    """With a mix, only renders at/after the floor are eligible."""
    now = time.time()
    _write_video(str(videos_dir / "old" / "MyScene.mp4"), mtime=now - 900)
    fresh = _write_video(str(videos_dir / "new" / "MyScene.mp4"), mtime=now - 2)

    found = _find_rendered_video("MyScene", newer_than=now - 60)
    assert os.path.samefile(found, fresh)


def test_no_floor_preserves_legacy_behaviour(videos_dir):
    """Callers that pass no floor still find old renders (cache/resume path)."""
    now = time.time()
    old = _write_video(str(videos_dir / "MyScene.mp4"), mtime=now - 9999)

    found = _find_rendered_video("MyScene")
    assert found is not None and os.path.samefile(found, old)


# ---------------------------------------------------------------------------
# The floor is actually threaded through from the render call sites
# ---------------------------------------------------------------------------


def test_run_scene_threads_a_freshness_floor(tmp_path, monkeypatch):
    """runner.run_scene must reject a video that predates its own render."""
    import manimgen.validator.runner as runner

    scene = tmp_path / "s.py"
    scene.write_text("class MyScene:\n    pass\n")

    monkeypatch.setattr(runner, "validate_scene_inputs", lambda p: {"ok": True, "errors": [], "warnings": []})
    monkeypatch.setattr(
        runner, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(
        runner, "inspect_scene_file", lambda p: type("G", (), {"ok": True, "findings": []})()
    )
    monkeypatch.setattr("manimgen.paths.logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(runner, "build_manimgl_command", lambda p, c: ["true"])
    monkeypatch.setattr(runner, "get_render_env", lambda: {})

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: _R())

    captured = {}

    def _fake_find(class_name, newer_than=None):
        captured["newer_than"] = newer_than
        return None

    monkeypatch.setattr(runner, "_find_rendered_video", _fake_find)

    runner.run_scene(str(scene), "MyScene")

    assert captured.get("newer_than") is not None, (
        "run_scene did not pass a freshness floor — a stale render from a "
        "previous attempt can be returned as this render's output"
    )


def test_retry_run_and_capture_threads_a_freshness_floor(tmp_path, monkeypatch):
    """retry._run_and_capture must also reject pre-attempt renders."""
    import manimgen.validator.retry as retry

    scene = tmp_path / "s.py"
    scene.write_text("class MyScene:\n    pass\n")

    monkeypatch.setattr(
        retry, "precheck_and_autofix_file", lambda p: {"ok": True, "stderr": ""}
    )
    monkeypatch.setattr(retry, "build_manimgl_command", lambda p, c: ["true"])
    monkeypatch.setattr(retry, "get_render_env", lambda: {})

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(retry.subprocess, "run", lambda *a, **k: _R())

    captured = {}

    def _fake_find(class_name, newer_than=None):
        captured["newer_than"] = newer_than
        return None

    monkeypatch.setattr(retry, "_find_rendered_video", _fake_find)

    retry._run_and_capture(str(scene), "MyScene")

    assert captured.get("newer_than") is not None, (
        "_run_and_capture did not pass a freshness floor — a retry can "
        "validate and ship the pre-fix render"
    )
