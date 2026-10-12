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
It also stops before a plan can run into "extra usage" (overage) billing: a
call that reports isUsingOverage stops the run, and when the account could
bill overage the run stops at MANIMGEN_MAX_PLAN_UTILIZATION (default 90%) of
the 5-hour or 7-day allowance instead of crossing the limit.
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
import threading
import time
from datetime import datetime, timezone
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
    # Per-role model overrides (role -> alias or model ID). Empty: every role
    # uses the provider default above.
    "models": {},
    "ollama_model": "llama3.1",
    "ollama_base_url": "http://localhost:11434",
    # Ollama's default context window is smaller than the Director prompt, so
    # without an explicit num_ctx the prompt is silently truncated.
    "ollama_num_ctx": 32768,
}


def _parse_role_models(raw) -> dict[str, str]:
    """llm.models from config.yaml as {role: model}; anything unusable is dropped."""
    if not isinstance(raw, dict):
        return {}
    models = {}
    for role, model in raw.items():
        model = str(model).strip() if model is not None else ""
        if model:
            models[str(role).strip().lower()] = model
    return models


def _load_llm_config() -> dict:
    """Load LLM config from config.yaml, falling back to defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
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
            "models": _parse_role_models(llm_cfg.get("models")),
            "ollama_model": llm_cfg.get("ollama_model", _DEFAULTS["ollama_model"]),
            "ollama_base_url": str(
                llm_cfg.get("ollama_base_url", _DEFAULTS["ollama_base_url"])
            ).rstrip("/"),
            "ollama_num_ctx": int(
                llm_cfg.get("ollama_num_ctx", _DEFAULTS["ollama_num_ctx"])
            ),
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
    role: str | None = None,
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
                generation, which returns Python. Only Gemini enforces it
                natively. For anthropic, claude_cli and ollama this is NOT
                guaranteed JSON: the prompt must ask for JSON, and the only
                help given is stripping a surrounding markdown code fence.
                The text is not validated, so callers still need their own
                parse-and-retry (the planner has one).
        role:   What the call is for (see ROLES). Picks the model (env
                MANIMGEN_MODEL_<ROLE>, then llm.models in config.yaml, then the
                provider default) and labels the call in the usage ledger.
    """
    provider = _resolve_provider()

    if provider in _PAID_PROVIDERS and not _paid_api_allowed():
        raise PaidApiBlockedError(
            f"LLM_PROVIDER={provider} bills per token and paid API calls are "
            f"disabled. Use LLM_PROVIDER=claude_cli (Claude subscription) or "
            f"set {_ALLOW_PAID_ENV}=1 to allow paid calls."
        )

    if provider not in ("gemini", "anthropic", "claude_cli", "ollama"):
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")

    # The role goes to the provider helper only when there is one, so a call
    # without a role looks exactly as it did before roles existed.
    kw = {"role": role} if role else {}
    _tls.meta = {}
    before = dict(_plan_windows)
    started = time.monotonic()
    error: str | None = None
    try:
        if provider == "gemini":
            return _gemini(system, user, images or [], json_mode=json_mode, **kw)
        if provider == "anthropic":
            text = _anthropic(system, user, images or [], **kw)
        elif provider == "claude_cli":
            text = _claude_cli(system, user, images or [], **kw)
        else:
            text = _ollama(system, user, images or [], **kw)
        return _strip_json_fence(text) if json_mode else text
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        _record_call(
            role=role,
            provider=provider,
            model=_model_for(provider, role),
            duration_s=time.monotonic() - started,
            before=before,
            error=error,
        )


def _strip_json_fence(text: str) -> str:
    """Remove a ```json ... ``` wrapper that prompt-only JSON often arrives in."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _gemini(
    system: str,
    user: str,
    images: list[str],
    json_mode: bool = False,
    role: str | None = None,
) -> str:
    from google import genai
    from google.genai import types

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
        model=_model_for("gemini", role),
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text.strip()


def _anthropic(
    system: str, user: str, images: list[str], role: str | None = None
) -> str:
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
        model=_model_for("anthropic", role),
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


# Variables the `claude` child is allowed to inherit; everything else (API keys
# for other services, tokens, ANTHROPIC_BASE_URL, ...) is withheld. Matching is
# case-insensitive because Windows variable names are, and POSIX proxy variables
# come in both cases. The set must keep what Claude Code needs to start and to
# find the user's subscription login on Windows, macOS and Linux: executable
# lookup and Windows shell basics, home/config/temp locations, locale and
# terminal, the desktop session (D-Bus, for the Linux keyring), and proxy and
# certificate settings for networks that need them.
_CLI_ENV_ALLOWLIST = frozenset(
    {
        # Executable lookup and Windows shell basics
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        # Home, profile, config and temp locations
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "ALLUSERSPROFILE",
        "PUBLIC",
        "TEMP",
        "TMP",
        "TMPDIR",
        # Identity
        "USER",
        "USERNAME",
        "USERDOMAIN",
        "LOGNAME",
        "COMPUTERNAME",
        "SHELL",
        # Terminal, locale and display
        "TERM",
        "COLORTERM",
        "LANG",
        "LANGUAGE",
        "TZ",
        "__CF_USER_TEXT_ENCODING",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
        # Proxy and certificates
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "NODE_OPTIONS",
    }
)
# LC_* locale, XDG_* base directories, and Claude Code's own CLAUDE_* settings
# (CLAUDE_CONFIG_DIR, CLAUDE_CODE_GIT_BASH_PATH, CLAUDE_CODE_OAUTH_TOKEN for
# `claude setup-token` subscription logins, ...).
_CLI_ENV_ALLOW_PREFIXES = ("LC_", "XDG_", "CLAUDE_")
# Prefixes that switch Claude Code to another (paid) backend. These are removed
# even though CLAUDE_ is allowed, so a provider flag added in a newer release
# is still caught.
_CLI_ENV_DENY_PREFIXES = ("CLAUDE_CODE_USE_", "CLAUDE_CODE_SKIP_")


def _claude_cli_env() -> dict[str, str]:
    """Environment for the `claude` subprocess.

    Built from an allowlist (`_CLI_ENV_ALLOWLIST` plus a few prefixes), so
    unrelated secrets such as GEMINI_API_KEY or GITHUB_TOKEN, and billing
    redirects such as ANTHROPIC_BASE_URL, never reach the child. Paid-auth
    variables are removed on top of that so Claude Code bills the signed-in
    Claude plan rather than silently switching to per-token billing when a key
    happens to be set (e.g. from .env for the `anthropic` provider). CLAUDE.md
    loading is disabled so project/user memory files do not leak into the
    prompt.
    """
    drop = _CLI_PAID_AUTH_VARS | _CLI_PARENT_SESSION_VARS
    env = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if name in drop or upper.startswith(_CLI_ENV_DENY_PREFIXES):
            continue
        if upper in _CLI_ENV_ALLOWLIST or upper.startswith(_CLI_ENV_ALLOW_PREFIXES):
            env[name] = value
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


def _claude_cli(
    system: str, user: str, images: list[str], role: str | None = None
) -> str:
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
            _model_for("claude_cli", role),
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
            _ensure_plan_headroom()
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
            _check_plan_limits(stdout)
            result = _parse_claude_cli_result(stdout)
            _tls.meta = _cli_meta(result, attempt)
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


# Overage ("extra usage") bills money once a plan allowance is used up. Claude
# Code reports the state in a rate_limit_event on every call:
#   rate_limit_info.isUsingOverage   true once this call was billed as overage
#   rate_limit_info.overageStatus    "rejected" when overage is off for the
#                                    account; anything else means it could bill
#   rate_limit_info.unifiedWindows   {"five_hour": {"utilization": 0.08,
#                                    "resetsAt": <epoch>}, "seven_day": {...}}
# When overage is rejected, hitting a limit only blocks calls (free). When it
# is not, crossing the limit costs money, so stop with headroom to spare.
_PLAN_UTILIZATION_ENV = "MANIMGEN_MAX_PLAN_UTILIZATION"
_DEFAULT_PLAN_UTILIZATION = 0.90

# Last plan state seen in this process: window name -> (utilization, resetsAt),
# and whether overage could bill. Lets the next call be refused before spawning.
_plan_windows: dict[str, tuple[float, float]] = {}
_overage_possible = False
_plan_logged = False
_last_plan_info: dict = {}  # last rate_limit_info seen, for scripts/check_billing.py


def _plan_utilization_limit() -> float:
    raw = os.environ.get(_PLAN_UTILIZATION_ENV, "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_PLAN_UTILIZATION
    except ValueError:
        logger.warning(
            "[llm] ignoring invalid %s=%r, using %s",
            _PLAN_UTILIZATION_ENV,
            raw,
            _DEFAULT_PLAN_UTILIZATION,
        )
        return _DEFAULT_PLAN_UTILIZATION
    return min(max(value, 0.01), 1.0)


def _plan_block_message(window: str, utilization: float, resets_at: float) -> str:
    when = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(resets_at))
        if resets_at
        else "later"
    )
    return (
        f"Your Claude plan is at {utilization:.0%} of its {window} allowance and "
        "extra usage (overage) billing is not disabled on this account, so "
        "continuing could charge you money. Stopped to be safe. Options: wait "
        f"until the allowance resets ({when} local time), turn extra usage off in "
        "your Claude settings (then hitting the limit only pauses, it never "
        f"bills), raise {_PLAN_UTILIZATION_ENV}, or set {_ALLOW_PAID_ENV}=1."
    )


def _ensure_plan_headroom() -> None:
    """Refuse to start a call when the last known plan state says it could bill."""
    if _paid_api_allowed() or not _overage_possible:
        return
    limit = _plan_utilization_limit()
    now = time.time()
    for window, (utilization, resets_at) in _plan_windows.items():
        # A window that has reset since we last looked is no longer full.
        if resets_at and resets_at <= now:
            continue
        if utilization >= limit:
            raise PaidApiBlockedError(
                _plan_block_message(window, utilization, resets_at)
            )


def _check_plan_limits(stdout: str) -> None:
    """Stop the run if a call used, or is about to run into, overage billing."""
    global _overage_possible, _plan_logged, _last_plan_info
    if _paid_api_allowed():
        return
    for event in _iter_cli_events(stdout):
        if event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info")
        if not isinstance(info, dict):
            continue
        if info.get("isUsingOverage") is True:
            raise PaidApiBlockedError(
                "Claude Code reports this call was billed as overage (extra "
                "usage), which costs money. Stopped. Turn extra usage off in "
                f"your Claude settings, or set {_ALLOW_PAID_ENV}=1 to allow it."
            )
        _last_plan_info = info
        # Anything but an explicit "rejected" (including a missing field) is
        # treated as "overage could bill": fail safe, not open.
        _overage_possible = info.get("overageStatus") != "rejected"
        windows = info.get("unifiedWindows")
        if isinstance(windows, dict):
            for name, w in windows.items():
                if isinstance(w, dict) and isinstance(
                    w.get("utilization"), (int, float)
                ):
                    _plan_windows[name] = (
                        float(w["utilization"]),
                        float(w.get("resetsAt") or 0),
                    )
        if not _plan_logged:
            _plan_logged = True
            logger.info(
                "[llm] plan: %s; overage %s (%s)",
                ", ".join(f"{n} {u:.0%}" for n, (u, _) in _plan_windows.items())
                or "usage unknown",
                info.get("overageStatus", "unknown"),
                info.get("overageDisabledReason", "n/a"),
            )
    _ensure_plan_headroom()


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


# ---------------------------------------------------------------------------
# Usage ledger: one JSONL record per chat() call
# ---------------------------------------------------------------------------
# Written to <logs_dir>/llm_usage.jsonl so one video's cost can be read back
# per role. The fields come from undocumented `claude -p` stream-json output
# (result.usage, result.total_cost_usd, rate_limit_event.unifiedWindows), so
# every one is parsed defensively and is null when absent. On a subscription
# `total_cost_usd` is a cost-equivalent at API prices, not money charged.
_LEDGER_NAME = "llm_usage.jsonl"
_PLAN_WINDOWS = ("five_hour", "seven_day")

_tls = threading.local()  # per-call data the provider helper hands back to chat()
_ledger_lock = threading.Lock()
_records: list[dict] = []  # this process's records, for usage_summary()


def _ledger_dir() -> str:
    from manimgen import paths

    return paths.logs_dir()


def _num(value) -> float | int | None:
    """A real number, or None (bool and numeric strings are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _cli_meta(result: dict | None, attempt: int) -> dict:
    """Usage numbers from a `claude -p` result event (all optional)."""
    if not isinstance(result, dict):
        return {"attempts": attempt}
    usage = result.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "attempts": attempt,
        "input_tokens": _num(usage.get("input_tokens")),
        "output_tokens": _num(usage.get("output_tokens")),
        "cache_read_tokens": _num(usage.get("cache_read_input_tokens")),
        "cache_creation_tokens": _num(usage.get("cache_creation_input_tokens")),
        "cost_usd_equiv": _num(result.get("total_cost_usd")),
        "num_turns": _num(result.get("num_turns")),
    }


def _record_call(
    *,
    role: str | None,
    provider: str,
    model: str,
    duration_s: float,
    before: dict,
    error: str | None,
) -> None:
    """Append this call to the ledger. Never raises: metrics must not fail a call."""
    try:
        meta = getattr(_tls, "meta", None) or {}
        rec: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "role": role,
            "provider": provider,
            "model": model,
            "duration_s": round(duration_s, 3),
            "ok": error is None,
            "error": error,
        }
        for key in (
            "attempts",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "cost_usd_equiv",
            "num_turns",
        ):
            rec[key] = meta.get(key)
        for name in _PLAN_WINDOWS:
            prev = before.get(name)
            now = _plan_windows.get(name) if provider == "claude_cli" else None
            rec[f"{name}_before"] = prev[0] if prev else None
            rec[f"{name}_after"] = now[0] if now else None
            rec[f"{name}_resets_at"] = now[1] if now else None
        with _ledger_lock:
            _records.append(rec)
            directory = _ledger_dir()
            os.makedirs(directory, exist_ok=True)
            # One write of one line; "a" appends atomically enough for the
            # single-process pipeline. newline="\n" keeps LF on Windows.
            with open(
                os.path.join(directory, _LEDGER_NAME),
                "a",
                encoding="utf-8",
                newline="\n",
            ) as f:
                f.write(json.dumps(rec, ensure_ascii=True) + "\n")
    except Exception as exc:  # noqa: BLE001 - the ledger is best effort
        logger.warning("[llm] could not write usage ledger: %s", exc)


def reset_usage() -> None:
    """Forget this process's in-memory ledger (the file is left alone)."""
    with _ledger_lock:
        _records.clear()


def _read_ledger_file(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def usage_summary(path: str | None = None) -> str:
    """Text summary of the run's LLM calls: per-role totals and plan usage.

    Covers the calls this process made, or the records in the JSONL file at
    `path`. Call it once at the end of a run and print it; it shows what share
    of the 5-hour and 7-day plan allowance the run used.
    """
    if path is not None:
        try:
            records = _read_ledger_file(path)
        except OSError as exc:
            return f"LLM usage: could not read {path}: {exc}"
    else:
        with _ledger_lock:
            records = list(_records)
    if not records:
        return "LLM usage: no LLM calls recorded."

    def total(items, key):
        values = [r[key] for r in items if _num(r.get(key)) is not None]
        return sum(values) if values else None

    def fmt(value, spec="{:,.0f}"):
        return "-" if value is None else spec.format(value)

    by_role: dict[str, list[dict]] = {}
    for rec in records:
        by_role.setdefault(str(rec.get("role") or "(none)"), []).append(rec)

    failed = sum(1 for r in records if not r.get("ok", True))
    n = len(records)
    lines = [
        f"LLM usage: {n} call{'s' if n != 1 else ''}"
        + (f", {failed} failed" if failed else "")
        + f", {fmt(total(records, 'duration_s'))}s in calls"
    ]
    lines.append(
        f"  {'role':<14}{'calls':>6}{'in_tok':>10}{'out_tok':>10}"
        f"{'secs':>8}{'cost_eq_usd':>13}"
    )
    for role in sorted(by_role):
        items = by_role[role]
        lines.append(
            f"  {role:<14}{len(items):>6}"
            f"{fmt(total(items, 'input_tokens')):>10}"
            f"{fmt(total(items, 'output_tokens')):>10}"
            f"{fmt(total(items, 'duration_s')):>8}"
            f"{fmt(total(items, 'cost_usd_equiv'), '{:,.2f}'):>13}"
        )
    lines.append(
        f"  {'total':<14}{n:>6}"
        f"{fmt(total(records, 'input_tokens')):>10}"
        f"{fmt(total(records, 'output_tokens')):>10}"
        f"{fmt(total(records, 'duration_s')):>8}"
        f"{fmt(total(records, 'cost_usd_equiv'), '{:,.2f}'):>13}"
    )
    lines.append(
        "  (cost_eq_usd is the API-price equivalent, not money charged on a plan)"
    )

    for name in _PLAN_WINDOWS:
        label = {"five_hour": "5-hour", "seven_day": "7-day"}[name]
        seen = [r for r in records if _num(r.get(f"{name}_after")) is not None]
        if not seen:
            continue
        first, last = seen[0], seen[-1]
        if _num(first.get(f"{name}_before")) is not None:
            start, note = first[f"{name}_before"], ""
        else:
            start, note = first[f"{name}_after"], " (from the first call's reading)"
        end = last[f"{name}_after"]
        if first.get(f"{name}_resets_at") != last.get(f"{name}_resets_at"):
            lines.append(
                f"  {label} plan allowance: window reset during the run "
                f"(now {end:.1%}); delta not available"
            )
        else:
            lines.append(
                f"  {label} plan allowance: {start:.1%} -> {end:.1%} "
                f"({(end - start) * 100:+.1f} percentage points){note}"
            )
    return "\n".join(lines)


_PROVIDER_MODEL_KEY = {
    "claude_cli": "claude_cli_model",
    "anthropic": "anthropic_model",
    "gemini": "gemini_model",
    "ollama": "ollama_model",
}

# Roles call sites pass to chat(role=...). Any other string is accepted and
# simply uses the default model.
ROLES = (
    "researcher",
    "planner",
    "planner_pdf",
    "critic",
    "cue_refill",
    "director",
    "error_fix",
    "visual_fix",
    "layout_check",
)


def _model_for(provider: str, role: str | None = None) -> str:
    """Model to use for `role` on `provider`.

    Order: MANIMGEN_MODEL_<ROLE> env var, then llm.models[role] in config.yaml,
    then the provider's default model. An unknown or missing role, or an
    unusable value (empty, or starting with "-" so it could pass as a CLI flag),
    falls back to the default; it is never an error.
    """
    default = str(_LLM_CONFIG[_PROVIDER_MODEL_KEY[provider]])
    name = (role or "").strip().lower()
    if not name:
        return default
    env_name = "MANIMGEN_MODEL_" + "".join(
        c if c.isalnum() else "_" for c in name.upper()
    )
    configured = _LLM_CONFIG.get("models")
    candidates = (
        os.environ.get(env_name, ""),
        configured.get(name, "") if isinstance(configured, dict) else "",
    )
    for value in candidates:
        value = str(value).strip()
        if value and not value.startswith("-"):
            return value
        if value:
            logger.warning("[llm] ignoring unusable model %r for role %s", value, name)
    return default


def _ollama(system: str, user: str, images: list[str], role: str | None = None) -> str:
    import requests

    cfg = _LLM_CONFIG
    base_url = _validate_ollama_url(cfg["ollama_base_url"])
    user_msg: dict = {"role": "user", "content": user}
    if images:
        user_msg["images"] = images

    url = f"{base_url}/api/chat"
    payload = {
        "model": _model_for("ollama", role),
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
        "options": {"num_ctx": cfg["ollama_num_ctx"]},
    }

    last_exc: Exception | None = None
    for _ in range(_REQUEST_RETRY_ATTEMPTS):
        try:
            resp = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
            if 400 <= resp.status_code < 500:
                # A client error (404 model not found, 400 bad request) will not
                # change on retry: fail at once with the server's message.
                last_exc = RuntimeError(
                    f"Ollama HTTP {resp.status_code} from {url}: {resp.text}"
                )
                break
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama HTTP {resp.status_code} from {url}: {resp.text}"
                )
            return resp.json()["message"]["content"].strip()
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
    raise RuntimeError(f"Ollama request to {url} failed: {last_exc}")
