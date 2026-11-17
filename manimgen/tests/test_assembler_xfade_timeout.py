"""A crossfade that times out must fall back to a hard cut, not abort assembly."""

import subprocess

import pytest

from manimgen.renderer import assembler

pytestmark = pytest.mark.unit


def test_xfade_timeout_falls_back_to_hard_concat(tmp_path, monkeypatch):
    calls = []

    def slow_xfade(a, b, out):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=300)

    def hard_concat(paths, out):
        calls.append(list(paths))
        open(out, "wb").close()

    monkeypatch.setattr(assembler, "_xfade_pair", slow_xfade)
    monkeypatch.setattr(assembler, "_hard_concat", hard_concat)

    result = assembler._concat(["a.mp4", "b.mp4"], {1}, str(tmp_path))

    assert calls == [["a.mp4", "b.mp4"]]
    assert result.endswith("_xfade_001.mp4")
