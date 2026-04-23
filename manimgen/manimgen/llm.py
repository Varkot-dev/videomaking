"""
Shared LLM client. Switches between Gemini (dev/testing) and Anthropic (production).

Provider resolution order:
  1. LLM_PROVIDER env var  (highest priority)
  2. llm_provider key in config.yaml
  3. Falls back to "gemini"

Usage:
    from manimgen.llm import chat
    response = chat(system="...", user="...")
"""

import logging
import os

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

# Network resilience defaults. Both providers hang indefinitely on a flaky
# TLS handshake without explicit timeouts (MASTER GUIDELINES §4.2).
# 120s is comfortable for multi-second generations; 3 retries with
# exponential backoff survive transient blips without failing a whole run.
_REQUEST_TIMEOUT_SECONDS = 120.0
_REQUEST_RETRY_ATTEMPTS = 3

_DEFAULTS = {
    "llm_provider": "gemini",
    "gemini_model": "gemini-2.5-flash",
    "anthropic_model": "claude-sonnet-4-6",
    "anthropic_max_tokens": 4096,
}


def _load_llm_config() -> dict:
    """Load LLM config from config.yaml, falling back to defaults."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        llm_cfg = cfg.get("llm", {})
        return {
            "llm_provider":         str(cfg.get("llm_provider", _DEFAULTS["llm_provider"])).lower(),
            "gemini_model":         llm_cfg.get("gemini_model",         _DEFAULTS["gemini_model"]),
            "anthropic_model":      llm_cfg.get("anthropic_model",      _DEFAULTS["anthropic_model"]),
            "anthropic_max_tokens": int(llm_cfg.get("max_tokens",       _DEFAULTS["anthropic_max_tokens"])),
        }
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[llm] Could not read config.yaml (%s) — using defaults", exc)
        return dict(_DEFAULTS)


def _resolve_provider() -> str:
    env = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env:
        return env
    return _load_llm_config()["llm_provider"]


def chat(system: str, user: str, images: list[str] | None = None) -> str:
    """
    Call the active LLM provider.

    Args:
        system: System prompt string.
        user:   User message string.
        images: Optional list of base64-encoded PNG strings to include as
                vision inputs (sent before the text message).
    """
    provider = _resolve_provider()

    if provider == "gemini":
        return _gemini(system, user, images or [])
    elif provider == "anthropic":
        return _anthropic(system, user, images or [])
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def _gemini(system: str, user: str, images: list[str]) -> str:
    from google import genai
    from google.genai import types

    cfg = _load_llm_config()
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(
            timeout=int(_REQUEST_TIMEOUT_SECONDS * 1000),  # SDK takes milliseconds
            retry_options=types.HttpRetryOptions(
                attempts=_REQUEST_RETRY_ATTEMPTS,
                initial_delay=1.0,
                max_delay=30.0,
                exp_base=2.0,
            ),
        ),
    )

    contents: list = []
    for b64 in images:
        import base64
        contents.append(types.Part.from_bytes(
            data=base64.b64decode(b64),
            mime_type="image/png",
        ))
    contents.append(user)

    response = client.models.generate_content(
        model=cfg["gemini_model"],
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text.strip()


def _anthropic(system: str, user: str, images: list[str]) -> str:
    import anthropic

    cfg = _load_llm_config()
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_REQUEST_RETRY_ATTEMPTS,
    )

    content: list = []
    for b64 in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({"type": "text", "text": user})

    message = client.messages.create(
        model=cfg["anthropic_model"],
        max_tokens=cfg["anthropic_max_tokens"],
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return message.content[0].text.strip()
