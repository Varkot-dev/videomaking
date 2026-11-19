"""Cue length and frame arithmetic (pure, no ffmpeg)."""

import pytest

from manimgen.planner.segmenter import compute_segments, slice_durations
from manimgen.renderer.muxer import cue_frame_span, cue_start_times_from_durations
from manimgen.renderer.tts import WordTimestamp
from manimgen.types import CueSegment

SPANS = [3.1, 4.37, 2.9, 5.03]
PAUSE = 0.38


def _segments():
    total = sum(SPANS)
    onsets = [0.12, 3.1, 7.47, 10.37]
    words = []
    for i, onset in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else total - 0.1
        words.append(WordTimestamp(f"a{i}", onset, onset + 0.5))
        words.append(WordTimestamp(f"b{i}", onset + 0.6, nxt - PAUSE))
    return compute_segments(words, [0, 2, 4, 6], total), total


def test_slice_durations_tile_the_narration():
    segs, total = _segments()
    durs = slice_durations(segs)
    assert durs == pytest.approx(SPANS)
    assert sum(durs) == pytest.approx(total)
    # The word.end based durations leave a pause per cue uncovered.
    assert sum(s.duration for s in segs) < total - PAUSE


def test_slice_durations_single_cue_is_whole_audio():
    assert slice_durations([CueSegment(0, 1, 0.1, 7.5)]) == [7.5]


def test_cue_frame_span_carries_remainder_forward():
    starts = cue_start_times_from_durations(SPANS)
    spans = [cue_frame_span(s, d, 60) for s, d in zip(starts, SPANS)]
    for (f0, n0), (f1, _) in zip(spans, spans[1:]):
        assert f0 + n0 == f1  # contiguous
    assert spans[0][0] == 0
    # one rounding of the whole length, not a sum of per-cue roundings
    assert sum(n for _, n in spans) == round(sum(SPANS) * 60)


def test_cue_frame_span_never_empty():
    assert cue_frame_span(1.0, 0.001, 60)[1] == 1
