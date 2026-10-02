"""Tests for manimgen.llm — provider dispatch + network-resilience params.

These tests never hit the real network; they mock the SDK clients and assert
that timeout / retry arguments are passed through. This guards against a
regression of the infinite-SSL_read hang hit on 2026-04-22 (main at
e478fbd, pipeline stalled 11 minutes on research_topic()).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

import manimgen.llm as llm_mod
from manimgen.llm import (
    _REQUEST_RETRY_ATTEMPTS,
    _REQUEST_TIMEOUT_SECONDS,
    PaidApiBlockedError,
    _anthropic,
    _claude_cli,
    _gemini,
    _resolve_provider,
    _strip_json_fence,
    chat,
)


class TestGeminiClientConfigured:
    """_gemini() must construct genai.Client with explicit timeout + retry."""

    def test_passes_timeout_and_retry_options(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

        fake_genai = MagicMock()
        fake_types = MagicMock()

        fake_response = MagicMock()
        fake_response.text = "hello"
        fake_genai.Client.return_value.models.generate_content.return_value = (
            fake_response
        )

        with patch.dict(
            "sys.modules",
            {
                "google": MagicMock(genai=fake_genai),
                "google.genai": fake_genai,
                "google.genai.types": fake_types,
            },
        ):
            fake_genai.types = fake_types
            _gemini(system="sys", user="user", images=[])

        construct_call = fake_genai.Client.call_args
        assert construct_call is not None, "genai.Client was not instantiated"
        assert construct_call.kwargs["api_key"] == "fake-key-for-test"
        assert "http_options" in construct_call.kwargs, (
            "Client must be constructed with http_options= for timeout/retry."
        )

        http_opts_call = fake_types.HttpOptions.call_args
        assert http_opts_call is not None
        assert http_opts_call.kwargs["timeout"] == int(_REQUEST_TIMEOUT_SECONDS * 1000)
        assert "retry_options" in http_opts_call.kwargs

        retry_call = fake_types.HttpRetryOptions.call_args
        assert retry_call is not None
        assert retry_call.kwargs["attempts"] == _REQUEST_RETRY_ATTEMPTS


class TestGeminiJsonMode:
    """_gemini(json_mode=True) must request guaranteed-valid JSON from the model.

    Guards against the planner JSONDecodeError class (2026-05-25): Gemini emitted
    a syntactically invalid lesson plan (missing comma) and the regex repair could
    not fix it. response_mime_type='application/json' makes the model emit
    parseable JSON natively, eliminating the failure class.
    """

    def _run_gemini(self, monkeypatch, **kwargs):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
        fake_genai = MagicMock()
        fake_types = MagicMock()
        fake_response = MagicMock()
        fake_response.text = "{}"
        fake_genai.Client.return_value.models.generate_content.return_value = (
            fake_response
        )
        with patch.dict(
            "sys.modules",
            {
                "google": MagicMock(genai=fake_genai),
                "google.genai": fake_genai,
                "google.genai.types": fake_types,
            },
        ):
            fake_genai.types = fake_types
            _gemini(system="sys", user="user", images=[], **kwargs)
        return fake_types

    def test_json_mode_sets_response_mime_type(self, monkeypatch):
        fake_types = self._run_gemini(monkeypatch, json_mode=True)
        cfg_call = fake_types.GenerateContentConfig.call_args
        assert cfg_call is not None, "GenerateContentConfig was not constructed"
        assert cfg_call.kwargs.get("response_mime_type") == "application/json", (
            "json_mode=True must set response_mime_type='application/json'"
        )

    def test_default_does_not_force_json(self, monkeypatch):
        fake_types = self._run_gemini(monkeypatch)
        cfg_call = fake_types.GenerateContentConfig.call_args
        assert cfg_call is not None
        assert cfg_call.kwargs.get("response_mime_type") is None, (
            "Default chat() must NOT force JSON — scene/code generation returns Python."
        )

    def test_chat_forwards_json_mode_to_gemini(self, monkeypatch):
        """chat(json_mode=True) must thread through to the Gemini provider."""
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("MANIMGEN_ALLOW_PAID_API", "1")
        with patch("manimgen.llm._gemini", return_value="{}") as mock_gemini:
            chat(system="sys", user="user", json_mode=True)
        assert mock_gemini.call_args.kwargs.get("json_mode") is True, (
            "chat() must forward json_mode to _gemini()"
        )


class TestAnthropicClientConfigured:
    """_anthropic() must construct Anthropic client with explicit timeout + retries."""

    def test_passes_timeout_and_max_retries(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        fake_anthropic = MagicMock()
        fake_response = MagicMock()
        fake_response.content = [MagicMock(type="text", text="hello")]
        fake_anthropic.Anthropic.return_value.messages.create.return_value = (
            fake_response
        )

        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            _anthropic(system="sys", user="user", images=[])

        construct_call = fake_anthropic.Anthropic.call_args
        assert construct_call is not None, "Anthropic() was not instantiated"
        assert construct_call.kwargs["api_key"] == "fake-key-for-test"
        assert construct_call.kwargs["timeout"] == _REQUEST_TIMEOUT_SECONDS
        assert construct_call.kwargs["max_retries"] == _REQUEST_RETRY_ATTEMPTS


class TestResilienceConstants:
    """Named constants centralize the timeout/retry policy — one edit to tune both."""

    def test_timeout_is_bounded_and_nonzero(self):
        assert 30.0 <= _REQUEST_TIMEOUT_SECONDS <= 600.0, (
            f"Timeout {_REQUEST_TIMEOUT_SECONDS}s is outside sane bounds [30, 600]."
        )

    def test_retries_are_bounded(self):
        assert 1 <= _REQUEST_RETRY_ATTEMPTS <= 10


class TestProviderResolution:
    """chat() resolves provider from env var first, then config.yaml."""

    def test_env_var_overrides_config(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert _resolve_provider() == "anthropic"

    def test_env_var_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "GEMINI")
        assert _resolve_provider() == "gemini"

    def test_empty_env_var_falls_back_to_config(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "")
        assert _resolve_provider() in {"claude_cli", "gemini", "anthropic", "ollama"}

    def test_ollama_provider_resolves(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert _resolve_provider() == "ollama"

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "nonexistent")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            chat(system="sys", user="user")


class TestOllamaUrlSsrfGuard:
    """_validate_ollama_url rejects anything outside localhost/private range."""

    def test_localhost_allowed(self):
        from manimgen.llm import _validate_ollama_url

        assert _validate_ollama_url("http://localhost:11434") == (
            "http://localhost:11434"
        )

    def test_loopback_ip_allowed(self):
        from manimgen.llm import _validate_ollama_url

        assert _validate_ollama_url("http://127.0.0.1:11434")

    def test_private_range_allowed(self):
        from manimgen.llm import _validate_ollama_url

        assert _validate_ollama_url("http://192.168.1.5:11434")

    def test_public_ip_blocked(self):
        from manimgen.llm import _validate_ollama_url

        with pytest.raises(ValueError, match="non-local address"):
            _validate_ollama_url("http://8.8.8.8:11434")

    def test_public_hostname_blocked(self):
        from manimgen.llm import _validate_ollama_url

        # Resolve a public host to a public address deterministically.
        with patch("manimgen.llm.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            with pytest.raises(ValueError, match="non-local address"):
                _validate_ollama_url("http://evil.example.com:11434")

    def test_non_http_scheme_blocked(self):
        from manimgen.llm import _validate_ollama_url

        with pytest.raises(ValueError, match="http/https"):
            _validate_ollama_url("ftp://localhost:11434")

    def test_ollama_call_blocks_public_url(self, monkeypatch):
        """_ollama must refuse a public base URL before issuing any request."""
        import manimgen.llm as llm_mod

        monkeypatch.setitem(llm_mod._LLM_CONFIG, "ollama_base_url", "http://8.8.8.8")
        with patch("requests.post") as mock_post:
            with pytest.raises(ValueError, match="non-local address"):
                llm_mod._ollama("sys", "user", [])
            mock_post.assert_not_called()


class TestAnthropicResponseText:
    """_anthropic() must return text from every text block, whatever comes first."""

    def _call(self, monkeypatch, blocks, stop_reason="end_turn"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
        fake_anthropic = MagicMock()
        fake_response = MagicMock()
        fake_response.content = blocks
        fake_response.stop_reason = stop_reason
        fake_anthropic.Anthropic.return_value.messages.create.return_value = (
            fake_response
        )
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            return _anthropic(system="sys", user="user", images=[])

    def test_skips_non_text_first_block(self, monkeypatch):
        blocks = [
            MagicMock(type="thinking", spec=["type", "thinking"]),
            MagicMock(type="text", text="class A: pass"),
        ]
        assert self._call(monkeypatch, blocks) == "class A: pass"

    def test_joins_multiple_text_blocks(self, monkeypatch):
        blocks = [MagicMock(type="text", text="ab"), MagicMock(type="text", text="cd")]
        assert self._call(monkeypatch, blocks) == "abcd"

    def test_truncation_is_logged(self, monkeypatch, caplog):
        blocks = [MagicMock(type="text", text="partial")]
        with caplog.at_level("WARNING", logger="manimgen.llm"):
            self._call(monkeypatch, blocks, stop_reason="max_tokens")
        assert "max_tokens" in caplog.text


class TestStripJsonFence:
    @pytest.mark.parametrize(
        "raw",
        ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '  {"a": 1}  ', '{"a": 1}'],
    )
    def test_unwraps_to_bare_json(self, raw):
        assert _strip_json_fence(raw) == '{"a": 1}'

    def test_unterminated_fence_left_alone(self):
        assert _strip_json_fence('```json\n{"a": 1}') == '```json\n{"a": 1}'

    def test_chat_json_mode_strips_fence_for_non_gemini(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        with patch("manimgen.llm._claude_cli", return_value="```json\n[1]\n```"):
            assert chat(system="s", user="u", json_mode=True) == "[1]"

    def test_chat_leaves_code_fences_without_json_mode(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        reply = "```python\nx = 1\n```"
        with patch("manimgen.llm._claude_cli", return_value=reply):
            assert chat(system="s", user="u") == reply


def _result_line(text="ok", is_error=False, subtype="success"):
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": text}
    )


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout.encode(), stderr=stderr.encode()
    )


class TestClaudeCli:
    """_claude_cli() drives `claude -p`; subprocess is mocked, nothing is spawned."""

    @pytest.fixture(autouse=True)
    def _claude_on_path(self, monkeypatch):
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)

    def test_provider_resolves(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        assert _resolve_provider() == "claude_cli"

    def test_chat_dispatches_to_claude_cli(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        with patch("manimgen.llm._claude_cli", return_value="hi") as mock_cli:
            assert chat(system="s", user="u", images=["AAAA"]) == "hi"
        mock_cli.assert_called_once_with("s", "u", ["AAAA"])

    def test_missing_executable_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: None)
        with pytest.raises(RuntimeError, match="not found on PATH"):
            _claude_cli(system="s", user="u", images=[])

    def test_returns_result_text(self):
        stdout = '{"type":"system","subtype":"init"}\n' + _result_line("  scene  ")
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)):
            assert _claude_cli(system="s", user="u", images=[]) == "scene"

    def test_prompt_delivery_and_isolation(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs
            sys_file = cmd[cmd.index("--system-prompt-file") + 1]
            with open(sys_file, encoding="utf-8") as f:
                seen["system"] = f.read()
            return _completed(_result_line("done"))

        with patch.object(llm_mod, "_run_cli", side_effect=fake_run):
            _claude_cli(system="SYSTEM PROMPT é", user="USER TEXT", images=["QUJD"])

        cmd, kwargs = seen["cmd"], seen["kwargs"]
        assert cmd[0] == "/fake/bin/claude"
        assert "-p" in cmd and "--tools=" in cmd and "--strict-mcp-config" in cmd
        assert "--disable-slash-commands" in cmd
        assert cmd[cmd.index("--model") + 1] == llm_mod._LLM_CONFIG["claude_cli_model"]
        assert seen["system"] == "SYSTEM PROMPT é"
        # No prompt text on the command line (Windows ~32K argv limit).
        assert not any("USER TEXT" in part or "SYSTEM PROMPT" in part for part in cmd)

        msg = json.loads(kwargs["input"].decode("utf-8"))
        content = msg["message"]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] == "QUJD"
        assert content[-1] == {"type": "text", "text": "USER TEXT"}

        env = kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
        assert kwargs["cwd"] != os.getcwd()
        assert kwargs["timeout"] > 0

    def test_error_result_is_retried_then_raises(self):
        err = _completed(_result_line("rate limited", is_error=True, subtype="error"))
        with patch.object(llm_mod, "_run_cli", return_value=err) as run:
            with pytest.raises(RuntimeError, match="rate limited"):
                _claude_cli(system="s", user="u", images=[])
        assert run.call_count == _REQUEST_RETRY_ATTEMPTS

    def test_recovers_after_transient_failure(self):
        responses = [
            _completed(stderr="network blip", returncode=1),
            _completed(_result_line("second try")),
        ]
        with patch.object(llm_mod, "_run_cli", side_effect=responses):
            assert _claude_cli(system="s", user="u", images=[]) == "second try"

    def test_timeout_raises_after_retries(self):
        boom = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        with patch.object(llm_mod, "_run_cli", side_effect=boom):
            with pytest.raises(RuntimeError, match="timed out"):
                _claude_cli(system="s", user="u", images=[])

    def test_stderr_surfaces_when_no_result_event(self):
        bad = _completed(stderr="Invalid API key · Please run /login", returncode=1)
        with patch.object(llm_mod, "_run_cli", return_value=bad):
            with pytest.raises(RuntimeError, match="/login"):
                _claude_cli(system="s", user="u", images=[])


def _init_line(api_key_source="none"):
    return json.dumps(
        {"type": "system", "subtype": "init", "apiKeySource": api_key_source}
    )


class TestNoPaidApiGuard:
    """Per-token billing must be impossible unless explicitly allowed."""

    @pytest.fixture(autouse=True)
    def _paid_not_allowed(self, monkeypatch):
        monkeypatch.delenv("MANIMGEN_ALLOW_PAID_API", raising=False)
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")

    @pytest.mark.parametrize("provider", ["anthropic", "gemini"])
    def test_paid_provider_blocked_before_any_call(self, monkeypatch, provider):
        monkeypatch.setenv("LLM_PROVIDER", provider)
        with (
            patch("manimgen.llm._anthropic") as a,
            patch("manimgen.llm._gemini") as g,
        ):
            with pytest.raises(PaidApiBlockedError, match="MANIMGEN_ALLOW_PAID_API"):
                chat(system="s", user="u")
        a.assert_not_called()
        g.assert_not_called()

    @pytest.mark.parametrize("value", ["1", "true", "YES"])
    def test_paid_provider_allowed_with_opt_in(self, monkeypatch, value):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("MANIMGEN_ALLOW_PAID_API", value)
        with patch("manimgen.llm._anthropic", return_value="ok"):
            assert chat(system="s", user="u") == "ok"

    @pytest.mark.parametrize("provider", ["claude_cli", "ollama"])
    def test_free_providers_not_blocked(self, monkeypatch, provider):
        monkeypatch.setenv("LLM_PROVIDER", provider)
        with (
            patch("manimgen.llm._claude_cli", return_value="ok"),
            patch("manimgen.llm._ollama", return_value="ok"),
        ):
            assert chat(system="s", user="u") == "ok"

    def test_cli_env_strips_every_paid_auth_var(self, monkeypatch):
        for name in llm_mod._CLI_PAID_AUTH_VARS:
            monkeypatch.setenv(name, "x")
        env = llm_mod._claude_cli_env()
        assert not (llm_mod._CLI_PAID_AUTH_VARS & env.keys())

    def test_cli_subscription_login_accepted(self):
        stdout = _init_line("none") + "\n" + _result_line("fine")
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)):
            assert _claude_cli(system="s", user="u", images=[]) == "fine"

    @pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", "apiKeyHelper"])
    def test_cli_api_key_auth_refused_without_retry(self, source):
        stdout = _init_line(source) + "\n" + _result_line("billed")
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)) as run:
            with pytest.raises(PaidApiBlockedError, match=source):
                _claude_cli(system="s", user="u", images=[])
        assert run.call_count == 1, "must stop at once, not retry a paid call"

    def test_cli_api_key_auth_allowed_with_opt_in(self, monkeypatch):
        monkeypatch.setenv("MANIMGEN_ALLOW_PAID_API", "1")
        stdout = _init_line("apiKeyHelper") + "\n" + _result_line("ok")
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)):
            assert _claude_cli(system="s", user="u", images=[]) == "ok"


class TestClaudeCliRobustness:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.delenv("MANIMGEN_ALLOW_PAID_API", raising=False)
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
        self.sleeps: list[float] = []
        monkeypatch.setattr(llm_mod.time, "sleep", self.sleeps.append)

    def test_backoff_between_attempts(self):
        fail = _completed(stderr="network blip", returncode=1)
        with patch.object(llm_mod, "_run_cli", return_value=fail):
            with pytest.raises(RuntimeError):
                _claude_cli(system="s", user="u", images=[])
        assert self.sleeps == [
            llm_mod._CLI_RETRY_BACKOFF_SECONDS,
            2 * llm_mod._CLI_RETRY_BACKOFF_SECONDS,
        ]

    @pytest.mark.parametrize(
        "message",
        [
            "Claude AI usage limit reached|1760000000",
            "Invalid API key · Please run /login",
            "You are not logged in",
        ],
    )
    def test_unrecoverable_errors_are_not_retried(self, message):
        err = _completed(_result_line(message, is_error=True, subtype="error"))
        with patch.object(llm_mod, "_run_cli", return_value=err) as run:
            with pytest.raises(RuntimeError, match="not retryable"):
                _claude_cli(system="s", user="u", images=[])
        assert run.call_count == 1

    def test_truncated_reply_is_logged(self, caplog):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "partial",
                "stop_reason": "max_tokens",
            }
        )
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)):
            with caplog.at_level("WARNING", logger="manimgen.llm"):
                assert _claude_cli(system="s", user="u", images=[]) == "partial"
        assert "truncated" in caplog.text

    def test_parent_claude_session_vars_not_inherited(self, monkeypatch):
        for name in llm_mod._CLI_PARENT_SESSION_VARS:
            monkeypatch.setenv(name, "parent")
        monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", r"C:\Git\bin\bash.exe")
        env = llm_mod._claude_cli_env()
        assert not (llm_mod._CLI_PARENT_SESSION_VARS & env.keys())
        # Windows needs this one to find Git Bash, so it must survive.
        assert env["CLAUDE_CODE_GIT_BASH_PATH"] == r"C:\Git\bin\bash.exe"


class TestRunCliTimeout:
    """_run_cli() uses a real subprocess: a timeout must stop the whole tree."""

    def test_returns_output(self, tmp_path):
        proc = llm_mod._run_cli(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input=b"echo me",
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout=30,
        )
        assert proc.returncode == 0 and proc.stdout == b"echo me"

    def test_timeout_raises_promptly(self, tmp_path):
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            llm_mod._run_cli(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                input=b"",
                cwd=str(tmp_path),
                env=dict(os.environ),
                timeout=1,
            )
        assert time.monotonic() - started < 20

    def test_timeout_kills_grandchild_holding_the_pipe(self, tmp_path):
        """The real-world hang: `claude` is a shim that launches a child (node)
        which keeps stdout open after the shim itself is killed."""
        pid_file = tmp_path / "grandchild.pid"
        code = (
            "import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(c.pid))\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            llm_mod._run_cli(
                [sys.executable, "-c", code],
                input=b"",
                cwd=str(tmp_path),
                env=dict(os.environ),
                timeout=2,
            )
        assert time.monotonic() - started < 25, "hung on the orphaned grandchild"
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        assert not _pid_alive(pid), "grandchild survived the timeout"


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed child of a dead parent can linger as a zombie until reaped.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _limit_event(
    *,
    overage_status="rejected",
    using_overage=False,
    five_hour=0.1,
    seven_day=0.1,
    resets_at=None,
):
    resets_at = resets_at if resets_at is not None else time.time() + 3600
    return json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "overageStatus": overage_status,
                "overageDisabledReason": "org_level_disabled",
                "isUsingOverage": using_overage,
                "unifiedWindows": {
                    "five_hour": {"utilization": five_hour, "resetsAt": resets_at},
                    "seven_day": {"utilization": seven_day, "resetsAt": resets_at},
                },
            },
        }
    )


class TestOverageGuard:
    """A Claude plan with "extra usage" on bills money past its allowance."""

    @pytest.fixture(autouse=True)
    def _clean_state(self, monkeypatch):
        monkeypatch.delenv("MANIMGEN_ALLOW_PAID_API", raising=False)
        monkeypatch.delenv("MANIMGEN_MAX_PLAN_UTILIZATION", raising=False)
        monkeypatch.setattr(llm_mod, "_plan_windows", {})
        monkeypatch.setattr(llm_mod, "_overage_possible", False)
        monkeypatch.setattr(llm_mod, "_plan_logged", False)
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)

    def _run(self, *events):
        stdout = "\n".join([_init_line("none"), *events, _result_line("fine")])
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)) as run:
            try:
                return _claude_cli(system="s", user="u", images=[]), run
            except PaidApiBlockedError as exc:
                return exc, run

    def test_overage_disabled_account_never_blocks_even_when_full(self):
        out, _ = self._run(_limit_event(overage_status="rejected", five_hour=0.99))
        assert out == "fine"

    def test_call_billed_as_overage_stops_the_run(self):
        out, _ = self._run(_limit_event(using_overage=True))
        assert isinstance(out, PaidApiBlockedError)
        assert "billed as overage" in str(out)

    @pytest.mark.parametrize("window", ["five_hour", "seven_day"])
    def test_stops_near_limit_when_overage_could_bill(self, window):
        out, _ = self._run(_limit_event(overage_status="allowed", **{window: 0.95}))
        assert isinstance(out, PaidApiBlockedError)
        assert window in str(out) and "95%" in str(out)

    def test_below_threshold_is_fine_even_if_overage_enabled(self):
        out, _ = self._run(_limit_event(overage_status="allowed", five_hour=0.5))
        assert out == "fine"

    def test_missing_overage_status_fails_safe(self):
        event = json.loads(_limit_event(five_hour=0.95))
        del event["rate_limit_info"]["overageStatus"]
        out, _ = self._run(json.dumps(event))
        assert isinstance(out, PaidApiBlockedError)

    def test_next_call_is_refused_without_starting_a_process(self):
        first, _ = self._run(_limit_event(overage_status="allowed", five_hour=0.95))
        assert isinstance(first, PaidApiBlockedError)
        with patch.object(llm_mod, "_run_cli") as run:
            with pytest.raises(PaidApiBlockedError):
                _claude_cli(system="s", user="u", images=[])
        run.assert_not_called()

    def test_reset_window_no_longer_blocks(self):
        past = time.time() - 10
        llm_mod._overage_possible = True
        llm_mod._plan_windows["five_hour"] = (0.99, past)
        stdout = _init_line("none") + "\n" + _result_line("fine")
        with patch.object(llm_mod, "_run_cli", return_value=_completed(stdout)):
            assert _claude_cli(system="s", user="u", images=[]) == "fine"

    def test_threshold_is_configurable(self, monkeypatch):
        monkeypatch.setenv("MANIMGEN_MAX_PLAN_UTILIZATION", "0.5")
        out, _ = self._run(_limit_event(overage_status="allowed", five_hour=0.6))
        assert isinstance(out, PaidApiBlockedError)

    def test_invalid_threshold_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MANIMGEN_MAX_PLAN_UTILIZATION", "lots")
        assert llm_mod._plan_utilization_limit() == llm_mod._DEFAULT_PLAN_UTILIZATION

    def test_explicit_opt_in_disables_the_guard(self, monkeypatch):
        monkeypatch.setenv("MANIMGEN_ALLOW_PAID_API", "1")
        out, _ = self._run(_limit_event(using_overage=True))
        assert out == "fine"

    def test_garbage_events_do_not_crash(self):
        out, _ = self._run(
            json.dumps({"type": "rate_limit_event"}),
            json.dumps({"type": "rate_limit_event", "rate_limit_info": "nope"}),
        )
        assert out == "fine"


class TestSuiteNeverLaunchesRealClaude:
    """Guard against tests that spend plan allowance by starting `claude`."""

    def test_claude_is_unfindable_by_default_in_tests(self):
        import shutil

        assert shutil.which("claude") is None

    def test_unmocked_chat_fails_without_launching_anything(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        with patch.object(llm_mod, "_run_cli") as run:
            with pytest.raises(RuntimeError, match="not found on PATH"):
                chat(system="s", user="u")
        run.assert_not_called()
