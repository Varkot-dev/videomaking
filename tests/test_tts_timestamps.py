"""
Tests for word timestamp functionality in manimgen/renderer/tts.py.

All network calls are mocked — no actual TTS calls are made.
"""

import json
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from manimgen.renderer.tts import (
    WordTimestamp,
    generate_narration,
    save_timestamps,
    load_timestamps,
    cue_times,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TIMESTAMPS = [
    WordTimestamp(word="Binary",    start=0.113, end=0.513),
    WordTimestamp(word="search",    start=0.525, end=1.050),
    WordTimestamp(word="is",        start=1.238, end=1.338),
    WordTimestamp(word="an",        start=1.337, end=1.399),
    WordTimestamp(word="efficient", start=1.413, end=1.851),
    WordTimestamp(word="algorithm", start=1.863, end=2.388),
    WordTimestamp(word="It",        start=2.788, end=2.888),
    WordTimestamp(word="works",     start=2.900, end=3.125),
    WordTimestamp(word="by",        start=3.125, end=3.250),
    WordTimestamp(word="dividing",  start=3.250, end=3.650),
]


# ---------------------------------------------------------------------------
# WordTimestamp dataclass
# ---------------------------------------------------------------------------

class TestWordTimestamp:

    def test_fields(self):
        t = WordTimestamp(word="hello", start=1.0, end=1.5)
        assert t.word == "hello"
        assert t.start == 1.0
        assert t.end == 1.5

    def test_duration(self):
        t = WordTimestamp(word="hello", start=1.0, end=1.5)
        assert abs((t.end - t.start) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# generate_narration — mock the async stream
# ---------------------------------------------------------------------------

def _make_fake_stream(word_events, audio_bytes=b"FAKEAUDIO"):
    """Build an async generator that yields fake edge-tts stream chunks."""
    async def _fake_stream():
        yield {"type": "audio", "data": audio_bytes}
        for evt in word_events:
            yield {
                "type": "WordBoundary",
                "offset": int(evt["start"] * 10_000_000),
                "duration": int((evt["end"] - evt["start"]) * 10_000_000),
                "text": evt["word"],
            }
    return _fake_stream


class TestGenerateNarration:

    def test_returns_audio_path_and_timestamps(self, tmp_path):
        audio_path = str(tmp_path / "narration.mp3")
        word_events = [
            {"word": "Hello", "start": 0.1, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ]

        fake_communicate = MagicMock()
        fake_communicate.stream = _make_fake_stream(word_events)

        with patch("manimgen.renderer.tts.edge_tts.Communicate", return_value=fake_communicate):
            path, timestamps = generate_narration("Hello world", audio_path)

        assert path == audio_path
        assert os.path.exists(audio_path)
        assert len(timestamps) == 2
        assert timestamps[0].word == "Hello"
        assert abs(timestamps[0].start - 0.1) < 1e-6
        assert abs(timestamps[1].end - 0.9) < 1e-6

    def test_audio_bytes_written(self, tmp_path):
        audio_path = str(tmp_path / "narration.mp3")
        word_events = [{"word": "test", "start": 0.1, "end": 0.3}]

        fake_communicate = MagicMock()
        fake_communicate.stream = _make_fake_stream(word_events, audio_bytes=b"AUDIODATA")

        with patch("manimgen.renderer.tts.edge_tts.Communicate", return_value=fake_communicate):
            generate_narration("test", audio_path)

        with open(audio_path, "rb") as f:
            assert f.read() == b"AUDIODATA"

    def test_word_boundary_uses_wordboundary_mode(self, tmp_path):
        audio_path = str(tmp_path / "narration.mp3")

        fake_communicate = MagicMock()
        fake_communicate.stream = _make_fake_stream([])

        with patch("manimgen.renderer.tts.edge_tts.Communicate", return_value=fake_communicate) as mock_cls:
            generate_narration("test", audio_path)
            _, kwargs = mock_cls.call_args
            assert kwargs.get("boundary") == "WordBoundary"

    def test_empty_text_returns_empty_timestamps(self, tmp_path):
        audio_path = str(tmp_path / "narration.mp3")

        fake_communicate = MagicMock()
        fake_communicate.stream = _make_fake_stream([])

        with patch("manimgen.renderer.tts.edge_tts.Communicate", return_value=fake_communicate):
            _, timestamps = generate_narration("", audio_path)

        assert timestamps == []


# ---------------------------------------------------------------------------
# save_timestamps / load_timestamps — round-trip
# ---------------------------------------------------------------------------

class TestTimestampPersistence:

    def test_roundtrip(self, tmp_path):
        json_path = str(tmp_path / "timestamps.json")
        save_timestamps(SAMPLE_TIMESTAMPS, json_path)
        loaded = load_timestamps(json_path)

        assert len(loaded) == len(SAMPLE_TIMESTAMPS)
        for orig, restored in zip(SAMPLE_TIMESTAMPS, loaded):
            assert orig.word == restored.word
            assert abs(orig.start - restored.start) < 1e-9
            assert abs(orig.end - restored.end) < 1e-9

    def test_json_structure(self, tmp_path):
        json_path = str(tmp_path / "timestamps.json")
        save_timestamps(SAMPLE_TIMESTAMPS[:2], json_path)

        with open(json_path) as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert set(data[0].keys()) == {"word", "start", "end"}

    def test_empty_timestamps(self, tmp_path):
        json_path = str(tmp_path / "timestamps.json")
        save_timestamps([], json_path)
        loaded = load_timestamps(json_path)
        assert loaded == []


# ---------------------------------------------------------------------------
# cue_times
# ---------------------------------------------------------------------------

class TestCueTimes:

    def test_first_word_cue(self):
        times = cue_times(SAMPLE_TIMESTAMPS, [0])
        assert abs(times[0] - 0.113) < 1e-9

    def test_multiple_cues(self):
        times = cue_times(SAMPLE_TIMESTAMPS, [0, 6, 9])
        assert abs(times[0] - SAMPLE_TIMESTAMPS[0].start) < 1e-9
        assert abs(times[1] - SAMPLE_TIMESTAMPS[6].start) < 1e-9
        assert abs(times[2] - SAMPLE_TIMESTAMPS[9].start) < 1e-9

    def test_single_cue_midway(self):
        times = cue_times(SAMPLE_TIMESTAMPS, [5])
        assert abs(times[0] - SAMPLE_TIMESTAMPS[5].start) < 1e-9

    def test_out_of_range_raises(self):
        with pytest.raises(IndexError):
            cue_times(SAMPLE_TIMESTAMPS, [99])

    def test_negative_index_raises(self):
        with pytest.raises(IndexError):
            cue_times(SAMPLE_TIMESTAMPS, [-1])

    def test_empty_cue_list(self):
        assert cue_times(SAMPLE_TIMESTAMPS, []) == []

    def test_interval_durations_are_positive(self):
        # times should be monotonically increasing for sorted cue indices
        times = cue_times(SAMPLE_TIMESTAMPS, [0, 3, 7, 9])
        for i in range(len(times) - 1):
            assert times[i] < times[i + 1]
