"""Tests for manimgen.llm — provider dispatch + network-resilience params.

These tests never hit the real network; they mock the SDK clients and assert
that timeout / retry arguments are passed through. This guards against a
regression of the infinite-SSL_read hang hit on 2026-04-22 (main at
e478fbd, pipeline stalled 11 minutes on research_topic()).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from manimgen.llm import (
    _gemini,
    _anthropic,
    _resolve_provider,
    chat,
    _REQUEST_TIMEOUT_SECONDS,
    _REQUEST_RETRY_ATTEMPTS,
)


class TestGeminiClientConfigured:
    """_gemini() must construct genai.Client with explicit timeout + retry."""

    def test_passes_timeout_and_retry_options(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

        fake_genai = MagicMock()
        fake_types = MagicMock()

        fake_response = MagicMock()
        fake_response.text = "hello"
        fake_genai.Client.return_value.models.generate_content.return_value = fake_response

        with patch.dict(
            "sys.modules",
            {"google": MagicMock(genai=fake_genai), "google.genai": fake_genai,
             "google.genai.types": fake_types},
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


class TestAnthropicClientConfigured:
    """_anthropic() must construct Anthropic client with explicit timeout + retries."""

    def test_passes_timeout_and_max_retries(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        fake_anthropic = MagicMock()
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="hello")]
        fake_anthropic.Anthropic.return_value.messages.create.return_value = fake_response

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
        assert _resolve_provider() in {"gemini", "anthropic"}

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            chat(system="sys", user="user")
