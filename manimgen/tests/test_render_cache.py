"""
Tests for render cache invalidation logic in cli.py.

_render_is_fresh() must:
  - return False when the video does not exist
  - return False when the video exists but has no sidecar
  - return False when the sidecar hash doesn't match the current topic
  - return True only when the video exists AND the sidecar hash matches

Zero LLM calls, zero subprocess calls.
"""

import json
import logging
import re
import textwrap

from manimgen import cli
from manimgen.cli import (
    _cached_scene_blocking_freezes,
    _file_hash,
    _remove_cue_files,
    _render_is_fresh,
    _section_key,
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
        sidecar.write_text("oldtopic", encoding="utf-8")
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
        (scenes_dir / "section_01.py").write_text(textwrap.dedent(body), encoding="utf-8")
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


class TestSectionKey:
    """#66: the content key behind every cached per-section artifact."""

    SECTION = {
        "id": "section_01",
        "title": "Intro",
        "narration": "gradient descent walks downhill",
        "cue_word_indices": [0, 2],
        "cues": [{"index": 0, "visual": "axes"}, {"index": 1, "visual": "ball"}],
    }

    def test_same_content_same_key(self):
        reloaded = json.loads(json.dumps(self.SECTION))
        assert _section_key(self.SECTION, "t1", [1.0, 2.0]) == _section_key(
            reloaded, "t1", [1.0, 2.0]
        )

    def test_dict_order_does_not_matter(self):
        reordered = dict(reversed(list(self.SECTION.items())))
        assert _section_key(self.SECTION, "t1", None) == _section_key(
            reordered, "t1", None
        )

    def test_small_timing_jitter_still_hits(self):
        assert _section_key(self.SECTION, "t1", [1.0, 2.0]) == _section_key(
            self.SECTION, "t1", [1.004, 1.996]
        )

    def test_any_content_change_misses(self):
        base = _section_key(self.SECTION, "t1", [1.0, 2.0])
        for field, value in [
            ("narration", "bubble sort swaps"),
            ("cue_word_indices", [0, 1]),
            ("cues", [{"index": 0, "visual": "bars"}, {"index": 1, "visual": "ball"}]),
        ]:
            changed = dict(self.SECTION, **{field: value})
            assert _section_key(changed, "t1", [1.0, 2.0]) != base, field
        assert _section_key(self.SECTION, "t2", [1.0, 2.0]) != base
        assert _section_key(self.SECTION, "t1", [1.0, 2.5]) != base
        assert _section_key(self.SECTION, "t1", [1.0, 2.0, 0.5]) != base

    def test_key_is_filename_safe(self):
        key = _section_key({"id": "ü/..\\:*"}, "t", [1.0])
        assert re.fullmatch(r"[0-9a-f]{16}", key)


class TestSidecarRobustness:
    def test_empty_sidecar_is_stale(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        (tmp_path / "Section01Scene.mp4.hash").write_text("", encoding="utf-8")
        assert not _render_is_fresh(str(video), "abc12345")

    def test_undecodable_sidecar_is_stale(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        (tmp_path / "Section01Scene.mp4.hash").write_bytes(b"\xff\xfe\x00")
        assert not _render_is_fresh(str(video), "abc12345")

    def test_empty_video_is_stale(self, tmp_path):
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"")
        _write_hash_sidecar(str(video), "abc12345")
        assert not _render_is_fresh(str(video), "abc12345")

    def test_rewrite_replaces_existing_sidecar(self, tmp_path):
        # os.replace, not os.rename: on Windows a rename onto an existing
        # file raises, which would wedge every later cache write.
        video = tmp_path / "Section01Scene.mp4"
        video.write_bytes(b"fake video")
        _write_hash_sidecar(str(video), "first")
        _write_hash_sidecar(str(video), "second")
        assert _render_is_fresh(str(video), "second")
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "Section01Scene.mp4",
            "Section01Scene.mp4.hash",
        ]


class TestPdfHash:
    def test_same_path_different_bytes_differs(self, tmp_path):
        pdf = tmp_path / "notes.pdf"
        pdf.write_bytes(b"%PDF-1.4 version one")
        first = _file_hash(str(pdf))
        pdf.write_bytes(b"%PDF-1.4 version two")
        assert _file_hash(str(pdf)) != first

    def test_same_bytes_different_path_matches(self, tmp_path):
        a = tmp_path / "a.pdf"
        b = tmp_path / "sub dir" / "b.pdf"
        b.parent.mkdir()
        a.write_bytes(b"%PDF same")
        b.write_bytes(b"%PDF same")
        assert _file_hash(str(a)) == _file_hash(str(b))


class TestRemoveCueFiles:
    def test_removes_only_this_sections_cue_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.paths, "muxed_dir", lambda: str(tmp_path))
        doomed = [
            "section_1_cue00.mp4",
            "section_1_cue00.mp4.hash",
            "section_1_cue07_video.mp4",
        ]
        kept = [
            "section_10_cue00.mp4",
            "section_1_cue00.m4a",
            "section_1_cue00_final.mp4",
            "other_section_1_cue00.mp4",
        ]
        for name in doomed + kept:
            (tmp_path / name).write_bytes(b"x")
        _remove_cue_files("section_1", logging.getLogger("test"))
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted(kept)

    def test_missing_dir_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.paths, "muxed_dir", lambda: str(tmp_path / "nope"))
        _remove_cue_files("section_01", logging.getLogger("test"))
