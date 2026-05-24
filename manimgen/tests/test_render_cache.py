"""
Tests for render cache invalidation logic in cli.py.

_render_is_fresh() must:
  - return False when the video does not exist
  - return False when the video exists but has no sidecar
  - return False when the sidecar hash doesn't match the current topic
  - return True only when the video exists AND the sidecar hash matches

Zero LLM calls, zero subprocess calls.
"""

import textwrap

from manimgen import cli
from manimgen.cli import (
    _cached_scene_blocking_freezes,
    _render_is_fresh,
    _topic_hash,
    _write_hash_sidecar,
)


class TestRenderIsFresh:

    def test_false_when_video_missing(self, tmp_path):
        video = str(tmp_path / "Section01Scene.mp4")
        assert not _render_is_fresh(video, "abc12345")

    def test_false_when_no_sidecar(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        assert not _render_is_fresh(str(video), "abc12345")

    def test_false_when_sidecar_has_different_hash(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        sidecar = tmp_path / "Section01Scene.mp4.hash"
        sidecar.write_text("oldtopic")
        assert not _render_is_fresh(str(video), "newtopic")

    def test_true_when_hash_matches(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        _write_hash_sidecar(str(video), "abc12345")
        assert _render_is_fresh(str(video), "abc12345")

    def test_write_then_read_roundtrip(self, tmp_path):
        video = tmp_path / "Section02Scene.mp4"
        video.write_bytes(b"fake video")
        h = _topic_hash("gradient descent")
        _write_hash_sidecar(str(video), h)
        assert _render_is_fresh(str(video), h)
        assert not _render_is_fresh(str(video), _topic_hash("bubble sort"))


class TestTopicHash:

    def test_deterministic(self):
        assert _topic_hash("gradient descent") == _topic_hash("gradient descent")

    def test_different_topics_differ(self):
        assert _topic_hash("gradient descent") != _topic_hash("bubble sort")

    def test_returns_8_chars(self):
        assert len(_topic_hash("any topic")) == 8


class TestCachedSceneBlockingFreezes:
    """#24: the render-cache / --resume shortcut bypasses every quality gate.
    _cached_scene_blocking_freezes re-runs the zero-cost freeze check on the
    cached scene .py so a cached section with a multi-second freeze invalidates
    the cache instead of shipping unchecked."""

    def _write_scene(self, tmp_path, monkeypatch, body: str) -> dict:
        scenes_dir = tmp_path / "scenes"
        scenes_dir.mkdir()
        monkeypatch.setattr(cli.paths, "scenes_dir", lambda: str(scenes_dir))
        section = {"id": "section_01"}
        (scenes_dir / "section_01.py").write_text(textwrap.dedent(body))
        return section

    def test_real_freeze_in_cached_scene_is_detected(self, tmp_path, monkeypatch):
        # animation 2.0s vs narration 10.0s → 8s frozen tail
        section = self._write_scene(
            tmp_path,
            monkeypatch,
            """\
            # CUE 0 — 10.0s
            self.play(ShowCreation(curve), run_time=2.0)
            self.wait(0.01)
            """,
        )
        freezes = _cached_scene_blocking_freezes(section, [10.0])
        assert len(freezes) == 1
        assert "CUE 0" in freezes[0]

    def test_clean_cached_scene_has_no_freezes(self, tmp_path, monkeypatch):
        section = self._write_scene(
            tmp_path,
            monkeypatch,
            """\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)
            """,
        )
        assert _cached_scene_blocking_freezes(section, [3.0]) == []

    def test_dynamic_cached_scene_does_not_block(self, tmp_path, monkeypatch):
        # post-#23: a cached scene using run_time=variable is UNKNOWN, never a
        # freeze — must not invalidate an otherwise-fresh cache.
        section = self._write_scene(
            tmp_path,
            monkeypatch,
            """\
            # CUE 0 — 9.5s
            rt = 7.5
            self.play(ShowCreation(curve), run_time=rt)
            self.wait(2.0)
            """,
        )
        assert _cached_scene_blocking_freezes(section, [9.5]) == []

    def test_missing_scene_file_fails_open(self, tmp_path, monkeypatch):
        scenes_dir = tmp_path / "scenes"
        scenes_dir.mkdir()
        monkeypatch.setattr(cli.paths, "scenes_dir", lambda: str(scenes_dir))
        # no file written → unverifiable cache is honored (returns [])
        assert _cached_scene_blocking_freezes({"id": "section_01"}, [10.0]) == []

    def test_missing_id_fails_open(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.paths, "scenes_dir", lambda: str(tmp_path))
        assert _cached_scene_blocking_freezes({}, [10.0]) == []
