"""
Tests for render cache invalidation logic in cli.py.

_render_is_fresh() must:
  - return False when the video does not exist
  - return False when the video exists but has no sidecar
  - return False when the sidecar hash doesn't match the current topic
  - return True only when the video exists AND the sidecar hash matches

Zero LLM calls, zero subprocess calls.
"""

import os
import tempfile

import pytest

from manimgen.cli import _render_is_fresh, _write_hash_sidecar, _topic_hash


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
