"""#59: the cutter must never hand the muxer an empty cue clip.

When the Director under-counts loop time the render ends before the last cues
start. A plain ``-ss`` cut there gives a ~262-byte file with no video stream.
``cut_video_at_cues`` instead holds the last rendered frame for such cues.
ffmpeg is not run here: the cut and freeze functions are replaced with
recorders. The real-ffmpeg check is in test_media_integration.py.
"""

import pytest

from manimgen import paths
from manimgen.renderer import muxer


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setitem(paths._RENDERING, "fps", 30)
    calls = []

    def cut(video_path, start, dur, out_path, i):
        calls.append(("cut", i))
        return out_path

    def freeze(video_path, dur, out_path, i):
        calls.append(("freeze", i))
        return out_path

    monkeypatch.setattr(muxer, "_cut_one", cut)
    monkeypatch.setattr(muxer, "_freeze_one", freeze)
    muxer.clear_mismatch_log()
    return calls


def _run(monkeypatch, tmp_path, source_dur, starts, durs):
    monkeypatch.setattr(muxer, "_source_duration", lambda p: source_dur)
    return muxer.cut_video_at_cues(
        "v.mp4", starts, durs, str(tmp_path / "cuts"), "section_04"
    )


def test_cues_inside_render_are_cut(recorder, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, 3.0, [0.0, 1.0, 2.0], [1.0, 1.0, 1.0])
    assert sorted(recorder) == [("cut", 0), ("cut", 1), ("cut", 2)]
    assert muxer.get_mismatch_log() == []


def test_cue_starting_at_or_past_end_is_frozen(recorder, monkeypatch, tmp_path):
    outs = _run(monkeypatch, tmp_path, 2.0, [0.0, 2.0, 3.0], [2.0, 1.0, 1.5])
    assert sorted(recorder) == [("cut", 0), ("freeze", 1), ("freeze", 2)]
    log = sorted(muxer.get_mismatch_log(), key=lambda m: m["output_path"])
    assert [m["output_path"] for m in log] == outs[1:]
    assert [m["diff"] for m in log] == [1.0, 1.5]


def test_less_than_one_frame_left_is_frozen(recorder, monkeypatch, tmp_path):
    # 0.01s of video remains: under one frame at 30 fps, so the cut is empty.
    _run(monkeypatch, tmp_path, 2.0, [0.0, 1.99], [1.99, 1.0])
    assert sorted(recorder) == [("cut", 0), ("freeze", 1)]


def test_unknown_source_duration_keeps_plain_cut(recorder, monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, None, [0.0, 5.0], [5.0, 1.0])
    assert sorted(recorder) == [("cut", 0), ("cut", 1)]


def test_failed_freeze_falls_back_to_cut(recorder, monkeypatch, tmp_path):
    def broken_freeze(video_path, dur, out_path, i):
        recorder.append(("freeze", i))
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(muxer, "_freeze_one", broken_freeze)
    _run(monkeypatch, tmp_path, 1.0, [0.0, 1.0], [1.0, 1.0])
    assert sorted(recorder) == [("cut", 0), ("cut", 1), ("freeze", 1)]


def test_source_duration_missing_file_is_none(tmp_path):
    assert muxer._source_duration(str(tmp_path / "nope.mp4")) is None
