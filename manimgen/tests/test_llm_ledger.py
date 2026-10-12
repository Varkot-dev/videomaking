"""Tests for the llm usage ledger (R26). The claude subprocess is always mocked."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

import manimgen.llm as llm_mod
from manimgen.llm import PaidApiBlockedError, chat


def _result_line(text="ok", is_error=False, subtype="success"):
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": text}
    )


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout.encode(), stderr=stderr.encode()
    )


def _full_stdout(
    text="ok", five=0.05, seven=0.02, in_tok=120, out_tok=30, cost=0.00107, resets=0
):
    events = [
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "overageStatus": "rejected",
                "unifiedWindows": {
                    "five_hour": {"utilization": five, "resetsAt": resets},
                    "seven_day": {"utilization": seven, "resetsAt": resets},
                },
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "total_cost_usd": cost,
            "duration_ms": 1500,
            "num_turns": 1,
            "usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_input_tokens": 7,
                "cache_creation_input_tokens": 3,
            },
        },
    ]
    return "\n".join(json.dumps(e) for e in events)


def _ledger_path():
    return os.path.join(llm_mod._ledger_dir(), "llm_usage.jsonl")


def _ledger_lines():
    with open(_ledger_path(), encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


class TestUsageLedger:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(llm_mod, "_plan_windows", {})
        monkeypatch.setattr(llm_mod, "_overage_possible", False)
        monkeypatch.setattr(llm_mod, "_plan_logged", True)
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "claude_cli_model", "sonnet")

    def _call(self, role="director", **kw):
        with patch.object(
            llm_mod, "_run_cli", return_value=_completed(_full_stdout(**kw))
        ):
            return chat(system="s", user="u", role=role)

    def test_one_line_per_call_with_expected_fields(self):
        self._call("director")
        self._call("critic", five=0.06, seven=0.02)
        lines = _ledger_lines()
        assert len(lines) == 2
        rec = lines[0]
        assert rec["role"] == "director"
        assert rec["model"] == "sonnet"
        assert rec["provider"] == "claude_cli"
        assert rec["ok"] is True and rec["error"] is None
        assert rec["input_tokens"] == 120 and rec["output_tokens"] == 30
        assert rec["cache_read_tokens"] == 7 and rec["cache_creation_tokens"] == 3
        assert rec["cost_usd_equiv"] == pytest.approx(0.00107)
        assert rec["five_hour_after"] == pytest.approx(0.05)
        assert rec["seven_day_after"] == pytest.approx(0.02)
        assert rec["five_hour_before"] is None
        assert isinstance(rec["duration_s"], float) and rec["duration_s"] >= 0
        assert "ts" in rec
        assert lines[1]["role"] == "critic" and lines[1]["model"] == "sonnet"
        assert lines[1]["five_hour_before"] == pytest.approx(0.05)
        assert lines[1]["five_hour_after"] == pytest.approx(0.06)

    def test_role_none_is_recorded_as_null(self):
        self._call(role=None)
        assert _ledger_lines()[0]["role"] is None

    def test_failed_call_is_recorded_and_still_raises(self):
        err = json.dumps(
            {"type": "result", "subtype": "error", "is_error": True, "result": "boom"}
        )
        with patch.object(llm_mod, "_run_cli", return_value=_completed(err)):
            with pytest.raises(RuntimeError, match="boom"):
                chat(system="s", user="u", role="critic")
        rec = _ledger_lines()[0]
        assert rec["ok"] is False and "boom" in rec["error"]
        assert rec["role"] == "critic"

    def test_missing_usage_fields_are_null_not_errors(self):
        with patch.object(
            llm_mod, "_run_cli", return_value=_completed(_result_line("ok"))
        ):
            chat(system="s", user="u", role="critic")
        rec = _ledger_lines()[0]
        assert rec["input_tokens"] is None and rec["cost_usd_equiv"] is None
        assert rec["five_hour_after"] is None

    def test_garbage_usage_values_are_ignored(self):
        ev = json.loads(_result_line("ok"))
        ev.update(
            usage={"input_tokens": "lots", "output_tokens": None}, total_cost_usd="x"
        )
        with patch.object(llm_mod, "_run_cli", return_value=_completed(json.dumps(ev))):
            chat(system="s", user="u", role="critic")
        rec = _ledger_lines()[0]
        assert rec["input_tokens"] is None and rec["cost_usd_equiv"] is None

    def test_unwritable_ledger_never_breaks_the_call(self, monkeypatch, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setattr(llm_mod, "_ledger_dir", lambda: str(blocker / "sub"))
        assert self._call() == "ok"

    def test_non_ascii_error_text_round_trips_on_one_line(self):
        err = json.dumps(
            {"type": "result", "subtype": "e", "is_error": True, "result": "é\n日本"}
        )
        with patch.object(llm_mod, "_run_cli", return_value=_completed(err)):
            with pytest.raises(RuntimeError):
                chat(system="s", user="u", role="critic")
        with open(_ledger_path(), encoding="utf-8", newline="") as f:
            raw = f.read()
        assert raw.count("\n") == 1
        assert "日本" in json.loads(raw)["error"]

    def test_other_providers_are_recorded_without_usage(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with patch("manimgen.llm._ollama", return_value="x"):
            chat(system="s", user="u", role="critic")
        rec = _ledger_lines()[0]
        assert rec["provider"] == "ollama" and rec["input_tokens"] is None

    def test_paid_guard_refusal_is_not_a_call(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("MANIMGEN_ALLOW_PAID_API", raising=False)
        with pytest.raises(PaidApiBlockedError):
            chat(system="s", user="u", role="critic")
        assert not os.path.exists(_ledger_path())

    def test_summary_totals_per_role_and_plan_delta(self):
        self._call("director", five=0.05, in_tok=100, out_tok=10, cost=0.5)
        self._call("director", five=0.09, in_tok=200, out_tok=20, cost=0.25)
        self._call("critic", five=0.12, in_tok=50, out_tok=5, cost=0.0)
        text = llm_mod.usage_summary()
        assert "3 calls" in text
        rows = {ln.split()[0]: ln.split() for ln in text.splitlines() if ln.strip()}
        assert rows["director"][1:4] == ["2", "300", "30"]
        assert rows["critic"][1:4] == ["1", "50", "5"]
        # first call's own cost is unknown (no "before"), so the 5-hour delta
        # runs from the first reading: 5% -> 12%.
        assert "5-hour" in text and "+7.0" in text

    def test_summary_when_no_calls(self):
        assert "no LLM calls" in llm_mod.usage_summary()

    def test_summary_flags_window_reset(self):
        self._call("director", five=0.9, resets=1000)
        self._call("director", five=0.1, resets=99999)
        assert "reset" in llm_mod.usage_summary().lower()

    def test_summary_can_read_a_ledger_file(self):
        self._call("director")
        path = _ledger_path()
        llm_mod.reset_usage()
        assert "1 call" in llm_mod.usage_summary(path)
