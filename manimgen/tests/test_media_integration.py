"""Real-ffmpeg integration tests for the media path.

Every other media test mocks subprocess, so an ffmpeg flag, an ffprobe parsing
change or a filtergraph break was invisible to CI. These tests build tiny
synthetic clips with lavfi sources (one to three seconds, 160x90) and run the
real muxer, audio slicer, cutter and assembler on them, then check the result
with ffprobe. They are skipped when ffmpeg or ffprobe is not on PATH.
"""

import json
import re
import shutil
import subprocess

import pytest

from manimgen import paths
from manimgen.renderer import assembler, audio_slicer, muxer
from manimgen.types import CueSegment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg and ffprobe are required",
    ),
]

_TOL = 0.25  # seconds; container rounding and frame alignment


def _ffmpeg(*args):
    cmd = ["ffmpeg", "-y", "-v", "error", *map(str, args)]
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert r.returncode == 0, r.stderr


def _make_video(path, seconds, color="blue"):
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30",
        "-t", seconds, "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    )  # fmt: skip
    return str(path)


def _make_audio(path, seconds, freq=440):
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000",
        "-t", seconds, "-c:a", "aac", path,
    )  # fmt: skip
    return str(path)


def _probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _kinds(info):
    return [s["codec_type"] for s in info["streams"]]


def _duration(path):
    return float(_probe(path)["format"]["duration"])


def _max_volume_db(path):
    """Loudest sample in the audio track, in dB (about -91 or lower is silence)."""
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-vn", "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )  # fmt: skip
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    assert m, r.stderr[-400:]
    return float("-inf") if m.group(1) == "-inf" else float(m.group(1))


@pytest.fixture
def small_render(monkeypatch, tmp_path):
    """Tiny, fast render settings and an isolated videos dir (config only)."""
    monkeypatch.setitem(paths._RENDERING, "resolution", "320x180")
    monkeypatch.setitem(paths._RENDERING, "fps", 30)
    monkeypatch.setitem(paths._PATHS, "videos", str(tmp_path / "videos"))
    return tmp_path


def test_mux_freezes_video_when_audio_is_longer(tmp_path):
    video = _make_video(tmp_path / "v.mp4", 1)
    audio = _make_audio(tmp_path / "a.m4a", 2)
    out = muxer.mux_audio_video(video, audio, str(tmp_path / "out" / "m.mp4"))
    assert sorted(_kinds(_probe(out))) == ["audio", "video"]
    assert _duration(out) == pytest.approx(2.0, abs=_TOL)
    assert _max_volume_db(out) > -20  # the sine survived: not a silent track


def test_mux_pads_audio_when_video_is_longer(tmp_path):
    video = _make_video(tmp_path / "v.mp4", 2)
    audio = _make_audio(tmp_path / "a.m4a", 1)
    out = muxer.mux_audio_video(video, audio, str(tmp_path / "m.mp4"))
    assert sorted(_kinds(_probe(out))) == ["audio", "video"]
    assert _duration(out) == pytest.approx(2.0, abs=_TOL)
    assert _max_volume_db(out) > -20


def test_slice_audio_cuts_real_cue_slices(tmp_path):
    src = _make_audio(tmp_path / "full.m4a", 3)
    segs = [
        CueSegment(cue_index=0, total_cues=3, start_time=0.4, duration=0.6),
        CueSegment(cue_index=1, total_cues=3, start_time=1.0, duration=1.0),
        CueSegment(cue_index=2, total_cues=3, start_time=2.0, duration=1.0),
    ]
    outs = audio_slicer.slice_audio(src, segs, str(tmp_path / "slices"), "section_01")
    assert len(outs) == 3
    for out in outs:
        info = _probe(out)
        assert _kinds(info) == ["audio"]
        assert int(info["streams"][0]["sample_rate"]) == 48000
        assert _max_volume_db(out) > -20
    # Cue 0 is sliced from 0.0 (pre-speech silence kept), so it spans 0 -> 1.0;
    # the last cue runs to EOF.
    for out in outs:
        assert _duration(out) == pytest.approx(1.0, abs=_TOL)


def test_cut_video_at_cues_produces_video_only_clips(small_render):
    tmp_path = small_render
    src = _make_video(tmp_path / "scene.mp4", 3)
    outs = muxer.cut_video_at_cues(
        src, [0.0, 1.0], [1.0, 2.0], str(tmp_path / "cuts"), "section_01"
    )
    assert len(outs) == 2
    assert outs[0].endswith("section_01_cue00_video.mp4")
    assert outs[1].endswith("section_01_cue01_video.mp4")
    for out, want in zip(outs, (1.0, 2.0)):
        assert _kinds(_probe(out)) == ["video"]
        assert _duration(out) == pytest.approx(want, abs=_TOL)


def test_assemble_video_joins_sections_with_audio(small_render):
    tmp_path = small_render
    clips = []
    for name, color in (
        ("section_01_cue00.mp4", "red"),
        ("section_01_cue01.mp4", "green"),
        ("section_02_cue00.mp4", "blue"),
    ):
        v = _make_video(tmp_path / f"v_{name}", 1, color)
        a = _make_audio(tmp_path / f"a_{name}.m4a", 1)
        clips.append(muxer.mux_audio_video(v, a, str(tmp_path / "mux" / name)))
    out = assembler.assemble_video(clips, "Real: Title?")
    assert out.endswith("real-_title-.mp4")
    assert sorted(_kinds(_probe(out))) == ["audio", "video"]
    # 3 x 1s, minus the 0.3s crossfade at the single section boundary.
    assert _duration(out) == pytest.approx(3 - assembler._XFADE_DURATION, abs=0.4)
    assert _max_volume_db(out) > -20
    # Intermediates are cleaned up; only the final file remains in videos/.
    assert [p.name for p in (tmp_path / "videos").iterdir()] == ["real-_title-.mp4"]


def test_assemble_video_injects_silent_audio_for_video_only_clip(small_render):
    tmp_path = small_render
    with_audio = muxer.mux_audio_video(
        _make_video(tmp_path / "v.mp4", 1),
        _make_audio(tmp_path / "a.m4a", 1),
        str(tmp_path / "mux" / "section_01_cue00.mp4"),
    )
    video_only = _make_video(tmp_path / "section_01_cue01.mp4", 1, "green")
    assert assembler._has_audio_stream(with_audio) is True
    assert assembler._has_audio_stream(video_only) is False
    out = assembler.assemble_video([with_audio, video_only], "mixed")
    assert sorted(_kinds(_probe(out))) == ["audio", "video"]
    assert _duration(out) == pytest.approx(2.0, abs=_TOL)


def test_real_mux_replaces_existing_clip_and_leaves_no_temp(tmp_path):
    video = _make_video(tmp_path / "v.mp4", 1)
    audio = _make_audio(tmp_path / "a.m4a", 1)
    out_dir = tmp_path / "muxed"
    out_dir.mkdir()
    (out_dir / "m.mp4").write_bytes(b"stale")
    muxer.mux_audio_video(video, audio, str(out_dir / "m.mp4"))
    assert [p.name for p in out_dir.iterdir()] == ["m.mp4"]
    assert sorted(_kinds(_probe(out_dir / "m.mp4"))) == ["audio", "video"]
