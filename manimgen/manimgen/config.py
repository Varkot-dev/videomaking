"""The one place config.yaml is located, parsed, validated and defaulted.

Every module that needs a setting (cli, paths, llm, renderer.tts) reads it from
here, so the file is opened once, with one set of defaults and one failure mode.

Where the file comes from
    1. The ``MANIMGEN_CONFIG`` environment variable, when set (an explicit path).
    2. Otherwise ``config.yaml`` in the project folder, the folder that holds
       ``setup.py``. That is one level above this package, which exists only in a
       source checkout. The supported install is therefore the editable one
       (``pip install -e .``); a non-editable wheel does not carry config.yaml.

Failure policy
    A missing or unreadable file, malformed YAML, or a value of the wrong type
    raises :class:`ConfigError` naming the file and the problem. There is no
    silent fallback to defaults: running with settings the person did not choose
    (for example TTS quietly off) is worse than stopping with a clear message.
    Defaults only fill keys that are absent from a config that loaded fine.

Output paths
    Relative paths in the ``output:`` block resolve against the folder holding
    config.yaml, never against the working directory, so launching manimgen from
    any folder writes to the same place. Absolute paths are used as given.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ENV_VAR = "MANIMGEN_CONFIG"
CONFIG_FILENAME = "config.yaml"

# Documented defaults for keys missing from a config that parsed. They match the
# shipped config.yaml, so a pared-down config behaves like the shipped one.
DEFAULTS: dict = {
    "llm": {
        "gemini_model": "gemini-2.5-flash",
        "anthropic_model": "claude-sonnet-5-5",
        # Scene files from the Director prompt routinely exceed 4096 tokens;
        # 16000 stays under the SDK's non-streaming limit.
        "max_tokens": 16000,
        "claude_cli_model": "sonnet",
        "claude_cli_path": "claude",
        # Per-role model overrides (role -> alias or model ID).
        "models": {},
        "ollama_model": "llama3.1",
        "ollama_base_url": "http://localhost:11434",
        # Ollama's default context window truncates the Director prompt.
        "ollama_num_ctx": 32768,
    },
    "output": {
        "scenes_dir": "manimgen/output/scenes",
        "videos_dir": "manimgen/output/videos",
        "logs_dir": "manimgen/output/logs",
        "audio_dir": "manimgen/output/audio",
        "muxed_dir": "manimgen/output/muxed",
        "exports_dir": "manimgen/output/videos/exports",
        "plan_cache": "manimgen/output/plan.json",
    },
    "rendering": {
        "quality": "hd",
        "resolution": "1920x1080",
        "fps": 60,
        "max_retries": 3,
        "render_timeout_2d": 240,
        "render_timeout_3d": 360,
        "render_timeout_fallback": 180,
    },
    "tts": {
        "engine": "edge-tts",
        "voice": "en-US-AndrewMultilingualNeural",
        "enabled": True,
        "speed": "+5%",
        "proxy": None,
    },
}
DEFAULT_PROVIDER = "claude_cli"


class ConfigError(RuntimeError):
    """config.yaml is missing, unreadable, malformed or has a bad value."""


_cache: dict | None = None
_cache_key: str | None = None


def config_path() -> Path:
    """Absolute path of the config file that is in effect."""
    explicit = os.environ.get(ENV_VAR, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / CONFIG_FILENAME).resolve()


def project_root() -> Path:
    """Folder holding config.yaml; relative output paths resolve against it."""
    return config_path().parent


def reload() -> None:
    """Forget the cached parse (tests, or after the file or env var changed)."""
    global _cache, _cache_key
    _cache = None
    _cache_key = None


def _missing_message(path: Path) -> str:
    return (
        f"manimgen could not find its config file at {path}. manimgen reads "
        f"{CONFIG_FILENAME} from the project folder (the one holding setup.py), "
        f"which only exists in a source checkout, so install it with "
        f"'pip install -e .' from that folder. A non-editable wheel install is "
        f"not supported. To use a config file elsewhere, set {ENV_VAR} to its path."
    )


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(_missing_message(path)) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file {path} must be a mapping of settings at the top "
            f"level, got {type(raw).__name__}."
        )
    return raw


def _as(path: Path, key: str, value, kind, label: str):
    """Coerce ``value`` with ``kind``; a failure names the file and the key."""
    if isinstance(value, bool):
        raise ConfigError(f"{path}: {key} must be {label}, got {value!r}.")
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: {key} must be {label}, got {value!r}.") from None


def _section(path: Path, raw: dict, name: str) -> dict:
    value = raw.get(name)
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"{path}: '{name}' must be a block of settings, got {value!r}."
        )
    merged = dict(DEFAULTS[name])
    merged.update(value)
    return merged


def _parse_role_models(raw) -> dict[str, str]:
    """llm.models as {role: model}; unusable entries are dropped."""
    if not isinstance(raw, dict):
        return {}
    models = {}
    for role, model in raw.items():
        model = str(model).strip() if model is not None else ""
        if model:
            models[str(role).strip().lower()] = model
    return models


def _validate(path: Path, raw: dict) -> dict:
    root = path.parent
    cfg = {name: _section(path, raw, name) for name in DEFAULTS}
    cfg["llm_provider"] = str(raw.get("llm_provider") or DEFAULT_PROVIDER).lower()

    llm = cfg["llm"]
    for key in ("max_tokens", "ollama_num_ctx"):
        llm[key] = _as(path, f"llm.{key}", llm[key], int, "an integer")
    for key in ("claude_cli_model", "claude_cli_path"):
        llm[key] = str(llm[key])
    llm["ollama_base_url"] = str(llm["ollama_base_url"]).rstrip("/")
    llm["models"] = _parse_role_models(llm["models"])

    out = cfg["output"]
    for key, value in list(out.items()):
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{path}: output.{key} must be a folder path, got {value!r}."
            )
        # An absolute value wins; a relative one is anchored at config.yaml.
        out[key] = str(root / value)

    rend = cfg["rendering"]
    for key in ("fps", "max_retries"):
        rend[key] = _as(path, f"rendering.{key}", rend[key], int, "an integer")
    for key in ("render_timeout_2d", "render_timeout_3d", "render_timeout_fallback"):
        rend[key] = _as(path, f"rendering.{key}", rend[key], float, "a number")

    if not isinstance(cfg["tts"]["enabled"], bool):
        raise ConfigError(
            f"{path}: tts.enabled must be true or false, got {cfg['tts']['enabled']!r}."
        )
    return cfg


def load() -> dict:
    """The merged, validated settings (cached; the file is parsed once)."""
    global _cache, _cache_key
    path = config_path()
    if _cache is None or _cache_key != str(path):
        _cache = _validate(path, _read(path))
        _cache_key = str(path)
    return _cache


def section(name: str) -> dict:
    """A copy of one settings block: 'llm', 'output', 'rendering' or 'tts'."""
    return dict(load()[name])


def llm_provider() -> str:
    return load()["llm_provider"]
