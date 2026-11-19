"""Regression: no freeze at cue boundaries in the cut + mux + assemble path.

A vertical Short froze for about 0.4 s at every cue change. The scene video was
cut at the segmenter's durations (end of the last spoken word) while the audio
slice for the same cue ran to the next cue's first word, so every clip's audio
was longer than its video by the pause between the cues and the muxer padded
that gap with a frozen last frame.

These tests build a synthetic section with real ffmpeg: a 60 fps video whose
every frame is different, a synthetic narration, realistic word timestamps with
sentence pauses. They run the real segmenter, slicer, cutter, muxer and
assembler, then hash every decoded frame and fail on a run of identical ones.
Skipped when ffmpeg or ffprobe is missing.
"""

import logging
import os
import shutil
import subprocess

import pytest

from manimgen import cli, paths
from manimgen.planner.segmenter import compute_segments
from manimgen.renderer import assembler
from manimgen.renderer.audio_slicer import slice_audio
from manimgen.renderer.tts import WordTimestamp

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg and ffprobe are required",
    ),
]

FPS = 60
SIZE = "180x320"
# Cue lengths as the listener hears them (onset to next onset).
CUE_SPANS = [3.1, 4.37, 2.9, 5.03]
PRE_SPEECH = 0.12  # silence before the first word, as edge-tts gives
PAUSE = 0.38  # silence between the last word of a cue and the next cue's first
MAX_FREEZE_FRAMES = 2


def _run(cmd, timeout=120):
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout


def _synthetic_timestamps(spans):
    """Two words per cue; the cue's last word ends PAUSE before the next onset."""
    total = sum(spans)
    bounds = [0.0]
    for s in spans:
        bounds.append(bounds[-1] + s)
    onsets = [PRE_SPEECH, *bounds[1:-1]]
    words = []
    for i, onset in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else total - 0.1
        words.append(WordTimestamp(f"a{i}", onset, onset + 0.5))
        words.append(WordTimestamp(f"b{i}", onset + 0.6, nxt - PAUSE))
    return words, [2 * i for i in range(len(onsets))], total


def _make_video(path, seconds):
    """A section render: every frame differs (testsrc2 has a moving counter)."""
    _run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=s={SIZE}:r={FPS}", "-t", f"{seconds:.6f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", str(path)]
    )  # fmt: skip


def _make_narration(path, seconds):
    _run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=300:sample_rate=24000", "-t", f"{seconds:.6f}",
         "-c:a", "libmp3lame", str(path)]
    )  # fmt: skip


def frame_hashes(path):
    out = _run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"]
    )
    return [
        ln.split(",")[-1].strip()
        for ln in out.splitlines()
        if ln and not ln.startswith("#")
    ]


def longest_freeze(hashes):
    """Longest run of identical consecutive frames (1 = no repeated frame)."""
    best = run = 1
    for a, b in zip(hashes, hashes[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best


def duration(path, stream=None):
    cmd = ["ffprobe", "-v", "error", "-of", "csv=p=0"]
    if stream:
        cmd += ["-select_streams", stream, "-show_entries", "stream=duration"]
    else:
        cmd += ["-show_entries", "format=duration"]
    return float(_run([*cmd, str(path)], timeout=30).split()[0])


def build_section(tmp_path, section_id, spans, cue_durations_fn):
    """Cut + mux one section through the real cli path; return muxed clips."""
    stamps, cue_idx, total = _synthetic_timestamps(spans)
    narration = tmp_path / f"{section_id}.mp3"
    _make_narration(narration, total)
    segments = compute_segments(stamps, cue_idx, total)
    slices = slice_audio(
        str(narration), segments, str(tmp_path / "audio"), section_id, overwrite=True
    )
    cue_durations = cue_durations_fn(segments)
    render = tmp_path / f"{section_id}_render.mp4"
    _make_video(render, sum(cue_durations))  # the scene waits out every cue
    clips = cli._cut_and_mux(
        {"id": section_id, "title": section_id},
        1,
        str(render),
        segments,
        slices,
        cue_durations,
        logging.getLogger("test"),
        "key",
    )
    assert len(clips) == len(spans)
    return clips, total


@pytest.fixture
def short_render(monkeypatch, tmp_path):
    monkeypatch.setitem(paths._RENDERING, "resolution", SIZE)
    monkeypatch.setitem(paths._RENDERING, "fps", FPS)
    monkeypatch.setitem(paths._PATHS, "muxed", str(tmp_path / "muxed"))
    monkeypatch.setitem(paths._PATHS, "videos", str(tmp_path / "videos"))
    return tmp_path


def test_cue_clips_have_no_freeze(short_render):
    clips, total = build_section(
        short_render, "section_01", CUE_SPANS, cli._cue_durations
    )
    one_frame = 1.0 / FPS
    for clip, span in zip(clips, CUE_SPANS):
        assert longest_freeze(frame_hashes(clip)) <= MAX_FREEZE_FRAMES, clip
        assert duration(clip, "v") == pytest.approx(span, abs=2 * one_frame)
    assert sum(duration(c, "v") for c in clips) == pytest.approx(total, abs=one_frame)


def test_assembled_section_has_no_freeze_and_matches_narration(short_render):
    clips, total = build_section(
        short_render, "section_01", CUE_SPANS, cli._cue_durations
    )
    final = assembler.assemble_video(clips, "one section")
    hashes = frame_hashes(final)
    assert longest_freeze(hashes) <= MAX_FREEZE_FRAMES
    assert len(hashes) / FPS == pytest.approx(total, abs=1.0 / FPS)


def test_two_sections_have_no_freeze_at_cue_or_section_boundaries(short_render):
    clips = []
    for sid, spans in (("section_01", CUE_SPANS), ("section_02", [2.6, 3.33, 4.1])):
        c, _ = build_section(short_render, sid, spans, cli._cue_durations)
        clips += c
    final = assembler.assemble_video(clips, "two sections")
    assert longest_freeze(frame_hashes(final)) <= MAX_FREEZE_FRAMES
    assert os.path.exists(final)
