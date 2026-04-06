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

import os

import yaml
from dotenv import load_dotenv

load_dotenv()


def _resolve_provider() -> str:
    env = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env:
        return env
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return str(cfg.get("llm_provider", "gemini")).lower()
    except Exception:
        return "gemini"


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

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    contents: list = []
    for b64 in images:
        import base64
        contents.append(types.Part.from_bytes(
            data=base64.b64decode(b64),
            mime_type="image/png",
        ))
    contents.append(user)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text.strip()


def _anthropic(system: str, user: str, images: list[str]) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    content: list = []
    for b64 in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({"type": "text", "text": user})

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return message.content[0].text.strip()
