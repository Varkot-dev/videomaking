"""The offline narration engine (renderer/tts_kokoro.py).

Everything except the last class runs with a fake voice: no kokoro-onnx, no
model files and no network are needed. The real-synthesis test skips itself
unless kokoro_onnx is importable and the model files are in the folder named by
MANIMGEN_KOKORO_DIR (or the default cache folder).
"""

import importlib.util
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from manimgen import cli, config
from manimgen.planner.cue_parser import parse_cues
from manimgen.planner.segmenter import compute_segments
from manimgen.renderer import tts
from manimgen.renderer import tts_kokoro as K

SR = K.SAMPLE_RATE


class FakeSynth:
    """One second of tone per ten characters, silence padded on both ends."""

    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text):
        self.spoken.append(text)
        n = int(SR * len(text) / 10)
        tone = 0.3 * np.sin(np.linspace(0, 440 * 2 * np.pi * n / SR, n))
        pad = np.zeros(int(0.08 * SR))
        return np.concatenate([pad, tone, pad]).astype(np.float32)

    def weights(self, words):
        return K.char_weights(words)


# ---------------------------------------------------------------------------
# Splitting and word timing
# ---------------------------------------------------------------------------


class TestSplitting:
    def test_cues_cut_at_indices(self):
        words = "a b c d e f".split()
        assert K.split_cues(words, [0, 2, 5]) == [["a", "b"], ["c", "d", "e"], ["f"]]

    def test_bad_indices_are_cleaned(self):
        words = "a b c".split()
        assert K.split_cues(words, [2, 2, 9, -1]) == [["a", "b"], ["c"]]
        assert K.split_cues(words, None) == [words]

    def test_phrases_split_at_sentence_ends(self):
        words = "One two. Three four five.".split()
        assert K.split_phrases(words) == [["One", "two."], ["Three", "four", "five."]]

    def test_short_comma_phrase_is_not_split_off(self):
        words = "Yes, we can do this now.".split()
        assert K.split_phrases(words) == [words]

    def test_long_comma_phrase_is_split(self):
        words = "When we add the two, the total grows.".split()
        assert K.split_phrases(words) == [
            "When we add the two,".split(),
            "the total grows.".split(),
        ]

    def test_trailing_scrap_is_merged(self):
        words = "First we add the two, then".split()  # no final stop
        assert K.split_phrases(words) == [words]


class TestWordTiming:
    def test_spans_are_contiguous_and_exact_at_the_ends(self):
        out = K.estimate_word_spans(["a", "bb", "ccc"], [1, 2, 3], 1.0, 6.0)
        assert out[0].start == 1.0
        assert out[-1].end == 7.0
        assert [w.end for w in out[:-1]] == [w.start for w in out[1:]]

    def test_proportional_to_weight(self):
        out = K.estimate_word_spans(["a", "b"], [1, 3], 0.0, 4.0)
        assert out[0].end - out[0].start == pytest.approx(1.0)
        assert out[1].end - out[1].start == pytest.approx(3.0)

    def test_phoneme_weight_ignores_punctuation_and_stress(self):
        assert K.phoneme_weight("ˈhɛloʊ,") == 5.0
        assert K.phoneme_weight("...") == 1.0

    def test_char_weights_count_letters_and_digits(self):
        assert K.char_weights(["hi,", "25", "--"]) == [2.0, 2.0, 1.0]

    def test_trim_removes_the_same_silence_at_both_ends(self):
        tone = 0.5 * np.ones(SR // 2, dtype=np.float32)
        clip = np.concatenate([np.zeros(SR), tone, np.zeros(SR)])
        out = K.trim_silence(clip)
        # half a second of tone plus a 10 ms margin each side, within a frame
        assert abs(len(out) / SR - 0.52) < 0.012

    def test_trim_of_pure_silence_is_short(self):
        assert len(K.trim_silence(np.zeros(SR * 3, dtype=np.float32))) <= SR // 10


# ---------------------------------------------------------------------------
# Timeline: cue-exact concatenation and the sync contracts
# ---------------------------------------------------------------------------

NARRATION = "Why a triangle? Put squares on each side. [CUE] Add them up, and see. [CUE] That is all."


@pytest.fixture
def timeline():
    text, idx = parse_cues(NARRATION)
    words = text.split()
    return words, idx, K.build_timeline(words, idx, FakeSynth())


class TestTimeline:
    def test_one_timestamp_per_split_word(self, timeline):
        words, _, tl = timeline
        assert [t.word for t in tl.timestamps] == words

    def test_timestamps_are_monotonic_and_inside_the_audio(self, timeline):
        _, _, tl = timeline
        total = len(tl.samples) / SR
        prev = 0.0
        for t in tl.timestamps:
            assert t.start >= prev - 1e-9 and t.end >= t.start
            prev = t.end
        assert tl.timestamps[-1].end < total

    def test_cue_onsets_equal_cumulative_audio_lengths(self):
        text, idx = parse_cues(NARRATION)
        words = text.split()
        cues = K.split_cues(words, idx)
        tl = K.build_timeline(words, idx, FakeSynth())
        # Rebuild each cue's length independently of the timeline code.
        expected, cursor = [], K.LEAD_IN
        for ci, cue in enumerate(cues):
            expected.append(cursor)
            for pi, phrase in enumerate(K.split_phrases(cue)):
                clip = K.trim_silence(FakeSynth().speak(" ".join(phrase)))
                cursor += len(clip) / SR
                last = pi == len(K.split_phrases(cue)) - 1
                if last and ci == len(cues) - 1:
                    continue
                pause = K._pause_after(phrase[-1])
                cursor += max(pause, K._CUE_GAP_MIN) if last else pause
        assert tl.cue_onsets == pytest.approx(expected, abs=1e-4)
        for ci, start in enumerate(idx):
            assert tl.timestamps[start].start == pytest.approx(expected[ci])

    def test_each_cue_speaks_only_its_own_words(self):
        text, idx = parse_cues(NARRATION)
        synth = FakeSynth()
        K.build_timeline(text.split(), idx, synth)
        joined = " ".join(synth.spoken)
        assert joined == text
        # no phrase crosses a cue boundary
        cue_texts = [" ".join(c) for c in K.split_cues(text.split(), idx)]
        for phrase in synth.spoken:
            assert sum(phrase in c for c in cue_texts) >= 1

    def test_contracts_with_segmenter(self, timeline):
        words, idx, tl = timeline
        total = len(tl.samples) / SR
        # contract 1: cue 0 audio starts at 0.0, speech after a lead-in
        assert tl.timestamps[0].start == pytest.approx(K.LEAD_IN)
        segs = compute_segments(tl.timestamps, idx, total, clean_text=" ".join(words))
        assert len(segs) == len(idx)
        # contract 2: boundary is the last word's end, which lies before the
        # next cue onset, so a slice cut at the onset never clips a syllable
        for i in range(len(idx) - 1):
            last_word_end = tl.timestamps[idx[i + 1] - 1].end
            assert last_word_end < tl.cue_onsets[i + 1]
            assert segs[i].duration == pytest.approx(
                last_word_end - (0.0 if i == 0 else tl.cue_onsets[i])
            )
        assert sum(s.duration for s in segs) <= total + 1e-6
        # segment starts are the exact onsets
        assert [s.start_time for s in segs] == pytest.approx(tl.cue_onsets)

    def test_no_cue_indices_means_one_cue(self):
        tl = K.build_timeline("One two three.".split(), None, FakeSynth())
        assert len(tl.cue_onsets) == 1

    def test_unspeakable_token_still_gets_a_timestamp(self):
        words = ["Wait", "--", "then", "go."]
        tl = K.build_timeline(words, [0], FakeSynth())
        assert [t.word for t in tl.timestamps] == words


# ---------------------------------------------------------------------------
# Writing the file (needs ffmpeg with libmp3lame)
# ---------------------------------------------------------------------------


def _have_mp3_ffmpeg() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "libmp3lame" in out


@pytest.mark.skipif(not _have_mp3_ffmpeg(), reason="ffmpeg with libmp3lame needed")
class TestAudioFile:
    def test_writes_mp3_the_slicer_accepts(self, tmp_path):
        from manimgen.renderer.audio_slicer import slice_audio

        text, idx = parse_cues(NARRATION)
        out = str(tmp_path / "audio" / "section_01.mp3")
        path, stamps = K.generate_narration(
            text, out, cue_word_indices=idx, synth=FakeSynth()
        )
        assert path == out and os.path.getsize(out) > 0
        assert not [p for p in os.listdir(tmp_path / "audio") if "tmp" in p]
        duration = tts.get_audio_duration(out)
        assert abs(duration - (stamps[-1].end + K.TAIL)) < 0.08
        segs = compute_segments(stamps, idx, duration, clean_text=text)
        slices = slice_audio(
            out, segs, str(tmp_path / "audio"), "section_01", overwrite=True
        )
        assert len(slices) == len(idx) and all(os.path.getsize(s) > 0 for s in slices)

    def test_empty_narration_gives_silence_and_no_words(self, tmp_path):
        out = str(tmp_path / "e.mp3")
        _, stamps = K.generate_narration("  ", out, synth=FakeSynth())
        assert stamps == [] and os.path.getsize(out) > 0


# ---------------------------------------------------------------------------
# Cache folder
# ---------------------------------------------------------------------------


class TestCacheDir:
    def test_windows(self):
        env = {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}
        got = K.default_cache_dir("win32", env, "/home/me")
        assert got == Path(env["LOCALAPPDATA"]) / "manimgen" / "kokoro"

    def test_windows_without_localappdata(self):
        got = K.default_cache_dir("win32", {}, "/home/me")
        assert got == Path("/home/me/AppData/Local/manimgen/kokoro")

    def test_macos(self):
        got = K.default_cache_dir("darwin", {}, "/Users/me")
        assert got == Path("/Users/me/Library/Caches/manimgen/kokoro")

    def test_linux_xdg(self):
        got = K.default_cache_dir("linux", {"XDG_CACHE_HOME": "/x"}, "/home/me")
        assert got == Path("/x/manimgen/kokoro")

    def test_linux_default(self):
        got = K.default_cache_dir("linux", {}, "/home/me")
        assert got == Path("/home/me/.cache/manimgen/kokoro")

    def test_env_beats_config_beats_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv(K.ENV_MODEL_DIR, raising=False)
        assert K.model_dir({"model_dir": str(tmp_path / "cfg")}) == tmp_path / "cfg"
        assert K.model_dir({"model_dir": None}) == K.default_cache_dir()
        monkeypatch.setenv(K.ENV_MODEL_DIR, str(tmp_path / "env"))
        assert K.model_dir({"model_dir": str(tmp_path / "cfg")}) == tmp_path / "env"


# ---------------------------------------------------------------------------
# Model download (HTTP mocked)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, data: bytes, fail_after: int | None = None, status_error=None):
        self.data, self.fail_after, self.status_error = data, fail_after, status_error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def iter_content(self, chunk_size):
        cut = len(self.data) if self.fail_after is None else self.fail_after
        yield self.data[:cut]
        if self.fail_after is not None:
            raise K.requests.ConnectionError("connection reset")


@pytest.fixture
def small_models(monkeypatch):
    monkeypatch.setattr(K, "MODEL_FILES", {"m.onnx": 10, "v.bin": 6})
    monkeypatch.setattr(K, "_MODEL_FILE", "m.onnx")
    monkeypatch.setattr(K, "_VOICES_FILE", "v.bin")


class TestDownload:
    def test_downloads_missing_files_atomically(
        self, tmp_path, monkeypatch, small_models
    ):
        calls = []

        def get(url, **kw):
            calls.append((url, kw["timeout"]))
            return _Resp(b"x" * (10 if url.endswith("m.onnx") else 6))

        monkeypatch.setattr(K.requests, "get", get)
        model, voices = K.ensure_models(tmp_path / "d")
        assert model.read_bytes() == b"x" * 10 and voices.read_bytes() == b"x" * 6
        assert sorted(os.listdir(tmp_path / "d")) == ["m.onnx", "v.bin"]
        assert all(t == K._HTTP_TIMEOUT for _, t in calls)
        assert K.models_cached(tmp_path / "d")

    def test_present_files_are_not_downloaded_again(
        self, tmp_path, monkeypatch, small_models
    ):
        (tmp_path / "m.onnx").write_bytes(b"x" * 10)
        (tmp_path / "v.bin").write_bytes(b"x" * 6)
        monkeypatch.setattr(
            K.requests, "get", lambda *a, **k: pytest.fail("no download expected")
        )
        K.ensure_models(tmp_path)

    def test_wrong_size_never_gets_the_real_name(
        self, tmp_path, monkeypatch, small_models
    ):
        monkeypatch.setattr(K.requests, "get", lambda url, **k: _Resp(b"short"))
        with pytest.raises(K.KokoroUnavailable, match="expected"):
            K.ensure_models(tmp_path)
        assert os.listdir(tmp_path) == []

    def test_interrupted_download_leaves_no_partial_file(
        self, tmp_path, monkeypatch, small_models
    ):
        monkeypatch.setattr(
            K.requests, "get", lambda url, **k: _Resp(b"x" * 10, fail_after=4)
        )
        with pytest.raises(K.KokoroUnavailable, match="could not download"):
            K.ensure_models(tmp_path)
        assert os.listdir(tmp_path) == []

    def test_truncated_file_in_place_is_replaced(
        self, tmp_path, monkeypatch, small_models
    ):
        (tmp_path / "m.onnx").write_bytes(b"xx")  # wrong size: a failed older copy
        (tmp_path / "v.bin").write_bytes(b"x" * 6)
        monkeypatch.setattr(K.requests, "get", lambda url, **k: _Resp(b"y" * 10))
        K.ensure_models(tmp_path)
        assert (tmp_path / "m.onnx").read_bytes() == b"y" * 10

    def test_offline_error_says_what_to_do(self, tmp_path, monkeypatch, small_models):
        def get(url, **kw):
            raise K.requests.ConnectionError("Name or service not known")

        monkeypatch.setattr(K.requests, "get", get)
        with pytest.raises(K.KokoroUnavailable) as err:
            K.ensure_models(tmp_path)
        msg = str(err.value)
        assert "Name or service not known" in msg
        assert "HTTPS_PROXY" in msg and "github.com" in msg and str(tmp_path) in msg

    def test_http_error_status_is_reported(self, tmp_path, monkeypatch, small_models):
        bad = K.requests.HTTPError("403 Forbidden")
        monkeypatch.setattr(
            K.requests, "get", lambda url, **k: _Resp(b"", status_error=bad)
        )
        with pytest.raises(K.KokoroUnavailable, match="403"):
            K.ensure_models(tmp_path)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def _load(tmp_path, monkeypatch, tts_block: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"tts:\n{tts_block}\n", encoding="utf-8")
    monkeypatch.setenv(config.ENV_VAR, str(cfg))
    config.reload()
    try:
        return config.load()["tts"]
    finally:
        pass


@pytest.fixture(autouse=True)
def _reset_config():
    yield
    config.reload()


class TestConfig:
    def test_defaults(self, tmp_path, monkeypatch):
        t = _load(tmp_path, monkeypatch, "  enabled: true")
        assert t["engine"] == "edge-tts" and t["fallback_engine"] == "kokoro"
        assert t["kokoro"] == {"voice": "am_michael", "speed": 1.05, "model_dir": None}

    def test_partial_kokoro_block_keeps_other_defaults(self, tmp_path, monkeypatch):
        t = _load(tmp_path, monkeypatch, "  kokoro:\n    voice: af_heart")
        assert t["kokoro"]["voice"] == "af_heart" and t["kokoro"]["speed"] == 1.05

    @pytest.mark.parametrize(
        "block",
        [
            "  engine: elevenlabs",
            "  fallback_engine: edge-tts",
            "  kokoro:\n    speed: 5",
            "  kokoro:\n    speed: fast",
            "  kokoro:\n    voice: ''",
            "  kokoro: 3",
        ],
    )
    def test_bad_values_are_rejected(self, tmp_path, monkeypatch, block):
        with pytest.raises(config.ConfigError):
            _load(tmp_path, monkeypatch, block)

    def test_shipped_config_is_valid(self, monkeypatch):
        monkeypatch.delenv(config.ENV_VAR, raising=False)
        config.reload()
        t = config.load()["tts"]
        assert t["engine"] == "edge-tts"


# ---------------------------------------------------------------------------
# Fallback selection
# ---------------------------------------------------------------------------


@pytest.fixture
def engines(monkeypatch, tmp_path):
    """edge-tts fails with a network error; kokoro is a recorder."""
    seen = {"edge": 0, "kokoro": []}

    def edge(text, output_path, voice=None):
        seen["edge"] += 1
        raise RuntimeError("edge-tts failed after 3 attempts: x") from OSError(
            "Cannot connect to host speech.platform.bing.com"
        )

    def kokoro(text, output_path, cue_word_indices=None, synth=None):
        seen["kokoro"].append((text, cue_word_indices))
        return output_path, [tts.WordTimestamp("w", 0.0, 1.0)]

    monkeypatch.setattr(tts, "_generate_edge", edge)
    monkeypatch.setattr(K, "generate_narration", kokoro)
    monkeypatch.setattr(K, "is_installed", lambda: True)
    monkeypatch.setitem(tts._TTS_CFG, "engine", "edge-tts")
    monkeypatch.setitem(tts._TTS_CFG, "fallback_engine", "kokoro")
    monkeypatch.setitem(tts._TTS_CFG, "kokoro", {"voice": "am_michael", "speed": 1.05})
    return seen


class TestFallback:
    def test_network_failure_uses_kokoro_and_logs_one_line(
        self, engines, tmp_path, caplog
    ):
        with caplog.at_level("WARNING", logger="manimgen.renderer.tts"):
            _, stamps = tts.generate_narration(
                "one two", str(tmp_path / "a.mp3"), cue_word_indices=[0, 1]
            )
        assert stamps and engines["kokoro"] == [("one two", [0, 1])]
        lines = [r for r in caplog.records if "offline voice" in r.getMessage()]
        assert len(lines) == 1

    def test_cue_hint_reaches_the_offline_voice(self, engines, tmp_path):
        with tts.cue_hint([0, 3]):
            tts.generate_narration("one two three four", str(tmp_path / "a.mp3"))
        assert engines["kokoro"] == [("one two three four", [0, 3])]
        tts.generate_narration("x", str(tmp_path / "b.mp3"))
        assert engines["kokoro"][-1][1] is None

    def test_later_sections_skip_edge_tts(self, engines, tmp_path):
        tts.generate_narration("one", str(tmp_path / "a.mp3"))
        tts.generate_narration("two", str(tmp_path / "b.mp3"))
        assert engines["edge"] == 1 and len(engines["kokoro"]) == 2

    def test_not_installed_behaves_as_before(self, engines, monkeypatch, tmp_path):
        monkeypatch.setattr(K, "is_installed", lambda: False)
        with pytest.raises(RuntimeError, match="3 attempts"):
            tts.generate_narration("one", str(tmp_path / "a.mp3"))
        assert engines["kokoro"] == []

    def test_fallback_none_behaves_as_before(self, engines, monkeypatch, tmp_path):
        monkeypatch.setitem(tts._TTS_CFG, "fallback_engine", "none")
        with pytest.raises(RuntimeError, match="3 attempts"):
            tts.generate_narration("one", str(tmp_path / "a.mp3"))
        assert engines["kokoro"] == []

    def test_non_network_error_does_not_fall_back(self, engines, monkeypatch, tmp_path):
        def edge(text, output_path, voice=None):
            raise RuntimeError("bad input") from ValueError("nope")

        monkeypatch.setattr(tts, "_generate_edge", edge)
        with pytest.raises(RuntimeError, match="bad input"):
            tts.generate_narration("one", str(tmp_path / "a.mp3"))
        assert engines["kokoro"] == []

    def test_engine_kokoro_never_calls_edge_tts(self, engines, monkeypatch, tmp_path):
        monkeypatch.setitem(tts._TTS_CFG, "engine", "kokoro")
        tts.generate_narration("one", str(tmp_path / "a.mp3"))
        assert engines["edge"] == 0 and len(engines["kokoro"]) == 1

    def test_http_403_counts_as_a_service_failure(self):
        import aiohttp

        err = RuntimeError("edge-tts failed")
        err.__cause__ = aiohttp.WSServerHandshakeError(None, (), status=403)
        assert tts._is_service_failure(err)


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


class TestCacheKey:
    SECTION = {"title": "t", "narration": "a b", "cue_word_indices": [0]}

    def test_edge_keys_are_unchanged_by_this_feature(self):
        cfg = {"tts": {"engine": "edge-tts", "voice": "v", "speed": "+5%"}}
        assert cli._offline_voice_key(cfg["tts"]) == []

    def test_engine_switch_changes_the_run_hash(self):
        edge = {"tts": {"engine": "edge-tts", "voice": "v", "speed": "+5%"}}
        kok = {
            "tts": {
                "engine": "kokoro",
                "voice": "v",
                "speed": "+5%",
                "kokoro": {"voice": "am_michael", "speed": 1.05},
            }
        }
        assert cli._content_hash("h", edge) != cli._content_hash("h", kok)

    def test_kokoro_voice_and_speed_change_the_run_hash(self):
        def cfg(voice, speed):
            return {
                "tts": {
                    "engine": "kokoro",
                    "kokoro": {"voice": voice, "speed": speed},
                }
            }

        base = cli._content_hash("h", cfg("am_michael", 1.05))
        assert base != cli._content_hash("h", cfg("af_heart", 1.05))
        assert base != cli._content_hash("h", cfg("am_michael", 1.2))

    def test_fallback_in_effect_changes_the_section_key(self, monkeypatch):
        before = cli._section_key(self.SECTION, "h", [1.0])
        monkeypatch.setattr(tts, "_edge_unreachable", True)
        monkeypatch.setitem(
            tts._TTS_CFG, "kokoro", {"voice": "am_michael", "speed": 1.05}
        )
        assert cli._section_key(self.SECTION, "h", [1.0]) != before

    def test_voice_change_changes_the_section_key(self, monkeypatch):
        monkeypatch.setattr(tts, "_edge_unreachable", True)
        monkeypatch.setitem(tts._TTS_CFG, "kokoro", {"voice": "a", "speed": 1.05})
        one = cli._section_key(self.SECTION, "h", [1.0])
        monkeypatch.setitem(tts._TTS_CFG, "kokoro", {"voice": "b", "speed": 1.05})
        assert cli._section_key(self.SECTION, "h", [1.0]) != one


def test_importing_the_engine_does_not_import_kokoro_onnx():
    code = "import sys; import manimgen.renderer.tts_kokoro; print('kokoro_onnx' in sys.modules)"
    out = subprocess.run(
        [os.sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert out.stdout.strip().endswith("False"), out.stderr


# ---------------------------------------------------------------------------
# One real synthesis (skipped unless the package and model files are present)
# ---------------------------------------------------------------------------


# Read at import, before conftest points the variable at an empty folder.
_REAL_DIR = Path(os.environ.get(K.ENV_MODEL_DIR, "").strip() or K.default_cache_dir())


def _real_voice_ready() -> bool:
    return importlib.util.find_spec("kokoro_onnx") is not None and K.models_cached(
        _REAL_DIR
    )


@pytest.mark.skipif(
    not _real_voice_ready(),
    reason="kokoro-onnx or its model files not available "
    "(set MANIMGEN_KOKORO_DIR to a folder that has them)",
)
@pytest.mark.skipif(not _have_mp3_ffmpeg(), reason="ffmpeg with libmp3lame needed")
@pytest.mark.timeout(600)
def test_real_synthesis_two_cues(tmp_path, monkeypatch):
    from manimgen.renderer.audio_slicer import slice_audio

    monkeypatch.setenv(K.ENV_MODEL_DIR, str(_REAL_DIR))
    monkeypatch.setattr(K, "is_installed", lambda: True)

    text, idx = parse_cues(
        "Put a square on each side. [CUE] Nine plus sixteen is twenty five."
    )
    out = str(tmp_path / "section_01.mp3")
    _, stamps = K.generate_narration(text, out, cue_word_indices=idx)
    duration = tts.get_audio_duration(out)

    assert [s.word for s in stamps] == text.split()
    starts = [s.start for s in stamps]
    assert starts == sorted(starts) and all(s.end >= s.start for s in stamps)
    assert 0.0 < stamps[0].start < 0.5 and stamps[-1].end < duration
    assert 3.0 < duration < 15.0

    segs = compute_segments(stamps, idx, duration, clean_text=text)
    assert len(segs) == 2 and all(s.duration > 0.5 for s in segs)
    slices = slice_audio(out, segs, str(tmp_path), "section_01", overwrite=True)
    assert len(slices) == 2 and all(os.path.getsize(s) > 0 for s in slices)
    # The second slice starts at the cue onset: its length is the rest of the file.
    assert tts.get_audio_duration(slices[1]) == pytest.approx(
        duration - segs[1].start_time, abs=0.1
    )
