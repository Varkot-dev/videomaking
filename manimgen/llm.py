"""
Shared LLM client. Switches between Gemini (dev/testing) and Anthropic (production)
based on the LLM_PROVIDER env var or config.yaml setting.

Usage:
    from manimgen.llm import chat
    response = chat(system="...", user="...")
"""

import os
from dotenv import load_dotenv

load_dotenv()


def chat(system: str, user: str) -> str:
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()

    if provider == "gemini":
        return _gemini(system, user)
    elif provider == "anthropic":
        return _anthropic(system, user)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def _gemini(system: str, user: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text.strip()


def _anthropic(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text.strip()
