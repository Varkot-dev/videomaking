"""A TTS failure must not silently ship an unnarrated video (#73).

renderer/tts.py retries edge-tts and rejects empty output; cli.main() stops
after the narration phase (before any scene is generated, so only the
planning LLM calls are spent) when narration still fails, unless the user
passes --allow-silent, in which case the section ships silent, is flagged in
the summary and the run exits 3.

edge_tts.Communicate is faked; nothing touches the network.
"""

import json
import os
import sys

import pytest

import manimgen.renderer.tts as tts
import manimgen.validator.retry as retry
from manimgen import cli
from tests.test_cross_plan_cache import _Fakes, _plan

# Captured before any fixture swaps it for the #66 harness fake.
_REAL_GENERATE = tts.generate_narration

TWO = _plan(
    "narration",
    ["first section narration here", "second section narration here"],
    [[0], [0]],
)


class _Communicate:
    """edge_tts.Communicate stand-in: fails `failures` times, then speaks."""

    calls = 0
    failures = 0
    kwargs: dict = {}
    words = True

    def __init__(self, text, voice, **kw):
        type(self).calls += 1
        type(self).kwargs = kw
        self.text = text

    async def stream(self):
        if type(self).calls <= type(self).failures:
            raise OSError("Cannot connect to host speech.platform.bing.com")
        for i, word in enumerate(self.text.split()):
            yield {"type": "audio", "data": b"\xff\xf3" + b"\x00" * 30}
            if type(self).words:
                yield {
                    "type": "WordBoundary",
                    "offset": i * 5_000_000,
                    "duration": 4_000_000,
                    "text": word,
                }


@pytest.fixture
def communicate(monkeypatch):
    _Communicate.calls = 0
    _Communicate.failures = 0
    _Communicate.kwargs = {}
    _Communicate.words = True
    sleeps = []
    monkeypatch.setattr(tts.edge_tts, "Communicate", _Communicate)
    monkeypatch.setattr(tts.time, "sleep", sleeps.append)
    _Communicate.sleeps = sleeps
    return _Communicate


class TestGenerateNarrationRetries:
    def test_two_failures_then_success(self, communicate, tmp_path):
        communicate.failures = 2
        out = str(tmp_path / "a.mp3")
        path, stamps = tts.generate_narration("one two three", out)
        assert communicate.calls == 3
        assert [s.word for s in stamps] == ["one", "two", "three"]
        assert os.path.getsize(path) > 0
        assert len(communicate.sleeps) == 2  # backed off between attempts

    def test_always_failing_raises_after_three_attempts(self, communicate, tmp_path):
        communicate.failures = 99
        with pytest.raises(RuntimeError, match="3 attempts"):
            tts.generate_narration("one two", str(tmp_path / "a.mp3"))
        assert communicate.calls == 3

    def test_no_word_timestamps_is_a_failure(self, communicate, tmp_path):
        communicate.words = False
        with pytest.raises(RuntimeError, match="word timestamps"):
            tts.generate_narration("one two", str(tmp_path / "a.mp3"))
        assert communicate.calls == 3

    def test_proxy_from_config_is_passed(self, communicate, tmp_path, monkeypatch):
        monkeypatch.setitem(tts._TTS_CFG, "proxy", "http://proxy.example:8080")
        tts.generate_narration("one", str(tmp_path / "a.mp3"))
        assert communicate.kwargs["proxy"] == "http://proxy.example:8080"


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    f = _Fakes(tmp_path, monkeypatch)
    retry.reset_run_budget()
    return f


def _main(fakes, monkeypatch, plan, *argv) -> int:
    if plan is not None:
        fakes.plan = plan
    monkeypatch.setattr(sys, "argv", ["manimgen", *argv])
    fakes.assembled = []
    try:
        cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    return 0


def _manifest(fakes) -> dict:
    with open(
        os.path.join(fakes.dirs["videos"], "run_manifest.json"), encoding="utf-8"
    ) as f:
        return json.load(f)


class TestRunStopsBeforeSceneGeneration:
    def test_edge_tts_down_stops_before_any_codegen(
        self, fakes, monkeypatch, communicate, capsys
    ):
        """Through the real generate_narration: every attempt fails."""
        communicate.failures = 99
        monkeypatch.setattr(tts, "generate_narration", _REAL_GENERATE)
        rc = _main(fakes, monkeypatch, TWO, "narration")
        assert rc == 1
        assert fakes.codegen_calls == 0, "no scene may be generated"
        assert communicate.calls == 6  # 3 attempts for each section
        assert fakes.assembled == []
        m = _manifest(fakes)
        assert m["result"] == "narration_failed"
        assert m["output"] is None
        assert [s["status"] for s in m["sections"]] == ["errored", "errored"]
        assert "speech.platform.bing.com" in m["sections"][0]["reason"]
        out = capsys.readouterr().out
        assert "--allow-silent" in out
        assert "manimgen --resume" in out

    def test_one_failing_section_stops_the_whole_run(self, fakes, monkeypatch):
        real = fakes._generate_narration

        def gen(text, output_path, voice=None):
            if "second" in text:
                raise RuntimeError("edge-tts failed after 3 attempts: timeout")
            return real(text, output_path, voice)

        monkeypatch.setattr(tts, "generate_narration", gen)
        assert _main(fakes, monkeypatch, TWO, "narration") == 1
        assert fakes.codegen_calls == 0
        statuses = [s["status"] for s in _manifest(fakes)["sections"]]
        assert statuses == ["not_run", "errored"]

    def test_allow_silent_ships_silent_and_exits_3(self, fakes, monkeypatch):
        def gen(text, output_path, voice=None):
            raise RuntimeError("edge-tts failed after 3 attempts: timeout")

        monkeypatch.setattr(tts, "generate_narration", gen)
        rc = _main(fakes, monkeypatch, TWO, "narration", "--allow-silent")
        assert rc == 3
        assert fakes.codegen_calls == 2
        m = _manifest(fakes)
        assert [s["status"] for s in m["sections"]] == ["silent", "silent"]
        assert m["output"] is not None

    def test_empty_narration_is_not_a_tts_failure(self, fakes, monkeypatch):
        plan = _plan("quiet", ["spoken words here", "x"], [[0], [0]])
        plan["sections"][1]["narration"] = ""
        rc = _main(fakes, monkeypatch, plan, "quiet")
        # The plan itself has no words for section 2: it ships silent and is
        # flagged, but the run is not stopped for it.
        assert rc == 3
        statuses = [s["status"] for s in _manifest(fakes)["sections"]]
        assert statuses == ["ok", "silent"]
