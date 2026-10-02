"""
Shared LLM client. Switches between the Claude Code CLI (uses a Claude
subscription, no API key), Anthropic (API key), Gemini (API key) and Ollama
(free local testing).

Provider resolution order:
  1. LLM_PROVIDER env var  (highest priority)
  2. llm_provider key in config.yaml
  3. Falls back to "claude_cli"

The `claude_cli` provider shells out to `claude -p` (Claude Code in headless
mode), so calls are billed to the signed-in Claude plan instead of per-token
API usage. It needs Claude Code installed and logged in (`claude` on PATH).

No-spend guard: the per-token providers (`anthropic`, `gemini`) are refused
unless MANIMGEN_ALLOW_PAID_API=1 is set, and `claude_cli` refuses any reply
that Claude Code produced with an API key instead of the subscription login.
The `ollama` provider talks to a local Ollama server (default
http://localhost:11434) and needs no API key — use it to exercise pipeline
plumbing for free. Switching back to gemini/anthropic for a real
quality run is a one-line change (LLM_PROVIDER env var or config.yaml).

Usage:
    from manimgen.llm import chat
    response = chat(system="...", user="...")
"""

import ipaddress
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

# Network resilience defaults. Both providers hang indefinitely on a flaky
# TLS handshake without explicit timeouts.
# 300s covers a full scene file (several thousand output tokens); 3 retries
# with exponential backoff survive transient blips without failing a whole run.
_REQUEST_TIMEOUT_SECONDS = 300.0
_REQUEST_RETRY_ATTEMPTS = 3

# A `claude -p` call also starts a Node process and loads Claude Code before
# the model runs, so it gets a longer ceiling than a raw HTTP request.
_CLI_TIMEOUT_SECONDS = 600.0

# Pause before retrying a failed `claude -p` call: attempt N waits N * this.
_CLI_RETRY_BACKOFF_SECONDS = 2.0

# Failures a retry cannot fix (not logged in, plan allowance used up). Retrying
# would only start two more Claude Code processes that fail the same way.
_CLI_FATAL_MARKERS = (
    "/login",
    "not logged in",
    "invalid api key",
    "usage limit",
    "limit reached",
    "credit balance",
)

_DEFAULTS = {
    "llm_provider": "claude_cli",
    "gemini_model": "gemini-2.5-flash",
    "anthropic_model": "claude-sonnet-5-5",
    # Scene files from the Director prompt routinely exceed 4096 tokens, which
    # truncated them mid-file. 16000 stays under the SDK's non-streaming limit.
    "anthropic_max_tokens": 16000,
    "claude_cli_model": "sonnet",
    "claude_cli_path": "claude",
    "ollama_model": "llama3.1",
    "ollama_base_url": "http://localhost:11434",
}


def _load_llm_config() -> dict:
    """Load LLM config from config.yaml, falling back to defaults."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        llm_cfg = cfg.get("llm", {})
        return {
            "llm_provider": str(
                cfg.get("llm_provider", _DEFAULTS["llm_provider"])
            ).lower(),
            "gemini_model": llm_cfg.get("gemini_model", _DEFAULTS["gemini_model"]),
            "anthropic_model": llm_cfg.get(
                "anthropic_model", _DEFAULTS["anthropic_model"]
            ),
            "anthropic_max_tokens": int(
                llm_cfg.get("max_tokens", _DEFAULTS["anthropic_max_tokens"])
            ),
            "claude_cli_model": str(
                llm_cfg.get("claude_cli_model", _DEFAULTS["claude_cli_model"])
            ),
            "claude_cli_path": str(
                llm_cfg.get("claude_cli_path", _DEFAULTS["claude_cli_path"])
            ),
            "ollama_model": llm_cfg.get("ollama_model", _DEFAULTS["ollama_model"]),
            "ollama_base_url": str(
                llm_cfg.get("ollama_base_url", _DEFAULTS["ollama_base_url"])
            ).rstrip("/"),
        }
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[llm] Could not read config.yaml (%s) — using defaults", exc)
        return dict(_DEFAULTS)


# Parsed once at import time, mirroring paths._PATHS and tts._TTS_CFG. The
# previous behaviour re-read and re-parsed config.yaml on every chat() call
# (twice per call: once in _resolve_provider, once in the provider helper).
_LLM_CONFIG = _load_llm_config()


def _validate_ollama_url(url: str) -> str:
    """Reject Ollama base URLs that point outside localhost/private ranges.

    The Ollama base URL is operator-supplied (config.yaml / defaults) and is
    used verbatim in an outbound POST. Without validation a malicious or
    mistyped config could turn the pipeline into an SSRF vector against
    arbitrary internal services. We only allow loopback and RFC1918 private
    addresses — the only places a local Ollama server is ever reachable.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"ollama_base_url must use http/https, got {parsed.scheme!r}: {url}"
        )
    host = parsed.hostname
    if not host:
        raise ValueError(f"ollama_base_url has no host: {url}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"ollama_base_url host {host!r} does not resolve: {exc}")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # 0.0.0.0 / :: are classified is_private by ipaddress but are
        # unspecified (not a real loopback); reject them explicitly so the
        # guard means "a local Ollama is actually reachable here".
        if ip.is_unspecified:
            raise ValueError(
                f"ollama_base_url {url!r} resolves to unspecified address {ip} — "
                "use 127.0.0.1 or localhost explicitly (SSRF guard)."
            )
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            raise ValueError(
                f"ollama_base_url {url!r} resolves to non-local address {ip} — "
                "only localhost/private-range Ollama servers are allowed (SSRF guard)."
            )
    return url


# Providers that bill per token against an API key. `claude_cli` (Claude plan)
# and `ollama` (local) never do.
_PAID_PROVIDERS = frozenset({"anthropic", "gemini"})
_ALLOW_PAID_ENV = "MANIMGEN_ALLOW_PAID_API"


class PaidApiBlockedError(RuntimeError):
    """Raised instead of making a call that would be billed per token."""


def _paid_api_allowed() -> bool:
    return os.environ.get(_ALLOW_PAID_ENV, "").strip().lower() in {"1", "true", "yes"}


def _resolve_provider() -> str:
    env = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env:
        return env
    return _LLM_CONFIG["llm_provider"]


def chat(
    system: str,
    user: str,
    images: list[str] | None = None,
    json_mode: bool = False,
) -> str:
    """
    Call the active LLM provider.

    Args:
        system: System prompt string.
        user:   User message string.
        images: Optional list of base64-encoded PNG strings to include as
                vision inputs (sent before the text message).
        json_mode: When True, ask the provider to emit guaranteed-valid JSON
                (Gemini response_mime_type='application/json'). Use only for
                calls that expect a JSON object/array — never for code
                generation, which returns Python. Gemini enforces it natively;
                the other providers rely on the prompt and have any markdown
                code fence around the JSON stripped.
    """
    provider = _resolve_provider()

    if provider in _PAID_PROVIDERS and not _paid_api_allowed():
        raise PaidApiBlockedError(
            f"LLM_PROVIDER={provider} bills per token and paid API calls are "
            f"disabled. Use LLM_PROVIDER=claude_cli (Claude subscription) or "
            f"set {_ALLOW_PAID_ENV}=1 to allow paid calls."
        )

    if provider == "gemini":
        return _gemini(system, user, images or [], json_mode=json_mode)
    elif provider == "anthropic":
        text = _anthropic(system, user, images or [])
    elif provider == "claude_cli":
        text = _claude_cli(system, user, images or [])
    elif provider == "ollama":
        text = _ollama(system, user, images or [])
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
    return _strip_json_fence(text) if json_mode else text


def _strip_json_fence(text: str) -> str:
    """Remove a ```json ... ``` wrapper that prompt-only JSON often arrives in."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _gemini(system: str, user: str, images: list[str], json_mode: bool = False) -> str:
    from google import genai
    from google.genai import types

    cfg = _LLM_CONFIG
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

        contents.append(
            types.Part.from_bytes(
                data=base64.b64decode(b64),
                mime_type="image/png",
            )
        )
    contents.append(user)

    config_kwargs: dict = {"system_instruction": system}
    if json_mode:
        # Native structured output: the model emits parseable JSON instead of
        # us hoping the prompt-instructed JSON happens to be valid and patching
        # it with regex after the fact.
        config_kwargs["response_mime_type"] = "application/json"

    response = client.models.generate_content(
        model=cfg["gemini_model"],
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text.strip()


def _anthropic(system: str, user: str, images: list[str]) -> str:
    import anthropic

    cfg = _LLM_CONFIG
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_REQUEST_RETRY_ATTEMPTS,
    )

    content: list = []
    for b64 in images:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            }
        )
    content.append({"type": "text", "text": user})

    message = client.messages.create(
        model=cfg["anthropic_model"],
        max_tokens=cfg["anthropic_max_tokens"],
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    # Join every text block: content[0] is not guaranteed to be text (thinking
    # or other block types can precede it).
    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )
    if message.stop_reason == "max_tokens":
        logger.warning(
            "[llm] Anthropic response hit max_tokens=%d and is truncated — "
            "raise llm.max_tokens in config.yaml",
            cfg["anthropic_max_tokens"],
        )
    return text.strip()


# Variables that make Claude Code authenticate with something other than the
# subscription login: an API key / bearer token (billed per token) or a cloud
# provider account (Bedrock, Vertex, Foundry; billed by that cloud).
# Identity of the Claude Code session manimgen itself may be running inside. A
# child that inherits these attaches to the parent's session and socket.
_CLI_PARENT_SESSION_VARS = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_TEE_SDK_STDOUT",
    }
)

_CLI_PAID_AUTH_VARS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    }
)


def _claude_cli_env() -> dict[str, str]:
    """Environment for the `claude` subprocess.

    Paid-auth variables are removed so Claude Code bills the signed-in Claude
    plan rather than silently switching to per-token billing when a key happens
    to be set (e.g. from .env for the `anthropic` provider). CLAUDE.md loading
    is disabled so project/user memory files do not leak into the prompt.
    """
    drop = _CLI_PAID_AUTH_VARS | _CLI_PARENT_SESSION_VARS
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    return env


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill `proc` and everything it started.

    On Windows `claude` is usually a .cmd shim that launches node.exe, and
    killing only the shim leaves node holding the output pipe open, so a
    timed-out call would hang forever. taskkill /T takes the whole tree; on
    POSIX the child leads its own session so its process group can be killed.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()


def _run_cli(
    cmd: list[str], *, input: bytes, cwd: str, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout that really stops the whole process tree."""
    popen_kwargs: dict = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _claude_cli(system: str, user: str, images: list[str]) -> str:
    """Run one prompt through `claude -p` and return the reply text.

    The system prompt goes in a temp file and the user turn (text + images) on
    stdin as a stream-json message, so no prompt text is passed as a command
    line argument (Windows caps a command line at ~32K characters and the
    Director system prompt alone is larger). Tools and MCP servers are disabled:
    this is a plain completion, the model must not touch the filesystem.
    """
    cfg = _LLM_CONFIG
    exe = shutil.which(cfg["claude_cli_path"])
    if exe is None:
        raise RuntimeError(
            f"LLM_PROVIDER=claude_cli but {cfg['claude_cli_path']!r} was not found "
            "on PATH. Install Claude Code and run `claude` once to log in, or set "
            "llm.claude_cli_path in config.yaml."
        )

    content: list = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        }
        for b64 in images
    ]
    content.append({"type": "text", "text": user})
    stdin_msg = json.dumps(
        {"type": "user", "message": {"role": "user", "content": content}}
    )

    # An empty working directory keeps Claude Code from picking up anything
    # from the repo (CLAUDE.md, .claude/ settings, hooks).
    with tempfile.TemporaryDirectory(prefix="manimgen-claude-") as workdir:
        system_file = os.path.join(workdir, "system.md")
        with open(system_file, "w", encoding="utf-8") as f:
            f.write(system)
        cmd = [
            exe,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--system-prompt-file",
            system_file,
            "--model",
            cfg["claude_cli_model"],
            # One "--tools=" token rather than "--tools" "": an empty argv entry
            # does not survive the cmd.exe shim npm installs on Windows.
            "--tools=",
            "--strict-mcp-config",
            # Skills and plugins from the user's Claude Code setup must not
            # change a plain completion (or add tokens to every call).
            "--disable-slash-commands",
            "--no-session-persistence",
        ]

        last_error = ""
        for attempt in range(1, _REQUEST_RETRY_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(_CLI_RETRY_BACKOFF_SECONDS * (attempt - 1))
            try:
                proc = _run_cli(
                    cmd,
                    input=stdin_msg.encode("utf-8"),
                    cwd=workdir,
                    env=_claude_cli_env(),
                    timeout=_CLI_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {_CLI_TIMEOUT_SECONDS:.0f}s"
                logger.warning("[llm] claude -p attempt %d %s", attempt, last_error)
                continue

            stdout = proc.stdout.decode("utf-8", errors="replace")
            _ensure_subscription_auth(stdout)
            result = _parse_claude_cli_result(stdout)
            if result is not None and not result.get("is_error"):
                if result.get("stop_reason") == "max_tokens":
                    logger.warning(
                        "[llm] claude -p reply hit the output token limit and is "
                        "truncated"
                    )
                return str(result.get("result", "")).strip()

            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            if result is not None:
                last_error = str(result.get("result") or result.get("subtype"))
            else:
                last_error = (
                    stderr or stdout.strip() or f"exit code {proc.returncode}"
                )[-2000:]
            logger.warning("[llm] claude -p attempt %d failed: %s", attempt, last_error)
            if any(m in last_error.lower() for m in _CLI_FATAL_MARKERS):
                raise RuntimeError(f"claude -p failed (not retryable): {last_error}")

    raise RuntimeError(f"claude -p failed: {last_error}")


def _iter_cli_events(stdout: str):
    """Yield each JSON object event from `claude -p` stream-json output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _parse_claude_cli_result(stdout: str) -> dict | None:
    """Return the final `{"type": "result", ...}` event from stream-json output."""
    result = None
    for event in _iter_cli_events(stdout):
        if event.get("type") == "result":
            result = event
    return result


def _ensure_subscription_auth(stdout: str) -> None:
    """Refuse output Claude Code produced with an API key instead of the login.

    The init event reports where the credential came from: "none" means the
    OAuth subscription login. Anything else ("ANTHROPIC_API_KEY",
    "apiKeyHelper", ...) means the call was billed per token, e.g. because the
    user's Claude Code settings configure an apiKeyHelper. That call has
    already happened, so this stops the run before it makes any more. Paid
    auth is allowed only with MANIMGEN_ALLOW_PAID_API=1.
    """
    if _paid_api_allowed():
        return
    for event in _iter_cli_events(stdout):
        if event.get("type") == "system" and event.get("subtype") == "init":
            source = event.get("apiKeySource", "none")
            if source not in (None, "none"):
                raise PaidApiBlockedError(
                    f"Claude Code authenticated with {source!r} (per-token "
                    "billing) instead of your Claude subscription. Remove the "
                    "API key / apiKeyHelper from your Claude Code settings and "
                    f"run `claude` to log in, or set {_ALLOW_PAID_ENV}=1."
                )
            return


def _ollama(system: str, user: str, images: list[str]) -> str:
    import requests

    cfg = _LLM_CONFIG
    base_url = _validate_ollama_url(cfg["ollama_base_url"])
    user_msg: dict = {"role": "user", "content": user}
    if images:
        user_msg["images"] = images

    url = f"{base_url}/api/chat"
    payload = {
        "model": cfg["ollama_model"],
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
    }

    last_exc: Exception | None = None
    for _ in range(_REQUEST_RETRY_ATTEMPTS):
        try:
            resp = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama HTTP {resp.status_code} from {url}: {resp.text}"
                )
            return resp.json()["message"]["content"].strip()
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
    raise RuntimeError(f"Ollama request to {url} failed: {last_exc}")
