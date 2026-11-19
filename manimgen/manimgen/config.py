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

# manimgl accepts exactly these quality flags (`manimgl --help`): -l is 480p,
# -m is 720p, --hd is 1080p, --uhd is 4K. The configured name is mapped to one
# of them; building "--" + name produced invalid flags such as "--l".
RENDER_QUALITY_FLAGS = {
    "l": "-l",
    "low": "-l",
    "m": "-m",
    "medium": "-m",
    "hd": "--hd",
    "high": "--hd",
    "uhd": "--uhd",
    "4k": "--uhd",
}

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
        # Per-role thinking effort for claude_cli (role -> low|medium|high|xhigh|max).
        "efforts": {},
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
        # Sections generated and rendered at the same time. Each one is an LLM
        # call plus a manimgl render, so the limit is the machine's CPU/GPU and
        # the LLM plan's rate limits. 1 restores strictly sequential runs.
        "parallel_sections": 3,
        # Processes one scene is rendered in at once (joined afterwards):
        # "auto" uses the spare cores, 1 renders in one process.
        "parallel_segments": "auto",
        "render_timeout_2d": 240,
        "render_timeout_3d": 360,
        "render_timeout_fallback": 180,
    },
    "shorts": {
        # Optional notes on how the owner talks and explains; read by the
        # shorts planner so scripts sound like them. Relative to config.yaml.
        "voice_profile": "voice_profile.md",
        # Burned-in word captions (`--captions`, see renderer/captions.py).
        "captions": {
            "enabled": False,
            # Distance from the bottom edge to the caption's baseline, in px of
            # the 1080x1920 frame. 480 is the top of the Shorts UI.
            "margin_bottom_px": 488,
            "margin_left_px": 48,
            "margin_right_px": 192,
            "font_size_px": 64,
            "font": "Arial",
        },
        # How many independent scenes the Director writes per short; the
        # better of them continues through the pipeline. 1 turns it off.
        "drafts": 2,
    },
    "tts": {
        "engine": "edge-tts",
        "voice": "en-US-AndrewMultilingualNeural",
        "enabled": True,
        "speed": "+5%",
        "proxy": None,
        # Engine used when edge-tts cannot be reached: "kokoro" or "none".
        "fallback_engine": "kokoro",
        # Offline voice (kokoro-onnx, see requirements-voice.txt).
        "kokoro": {
            "voice": "am_michael",
            "speed": 1.05,
            "model_dir": None,
        },
    },
}
TTS_ENGINES = ("edge-tts", "kokoro")
TTS_FALLBACK_ENGINES = ("kokoro", "none")
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
    llm["efforts"] = _parse_role_models(llm["efforts"])

    out = cfg["output"]
    for key, value in list(out.items()):
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{path}: output.{key} must be a folder path, got {value!r}."
            )
        # An absolute value wins; a relative one is anchored at config.yaml.
        out[key] = str(root / value)

    shorts = cfg["shorts"]
    profile = shorts.get("voice_profile") or "voice_profile.md"
    shorts["voice_profile"] = str(root / str(profile))
    caps = shorts.get("captions")
    if not isinstance(caps, dict):
        raise ConfigError(f"{path}: shorts.captions must be a block of settings.")
    caps = {**DEFAULTS["shorts"]["captions"], **caps}
    if not isinstance(caps["enabled"], bool):
        raise ConfigError(
            f"{path}: shorts.captions.enabled must be true or false, "
            f"got {caps['enabled']!r}."
        )
    for key in (
        "margin_bottom_px",
        "margin_left_px",
        "margin_right_px",
        "font_size_px",
    ):
        caps[key] = _as(path, f"shorts.captions.{key}", caps[key], int, "an integer")
        if caps[key] < 0:
            raise ConfigError(f"{path}: shorts.captions.{key} must not be negative.")
    caps["font"] = str(caps["font"]).strip() or "Arial"
    shorts["captions"] = caps
    shorts["drafts"] = _as(path, "shorts.drafts", shorts["drafts"], int, "an integer")
    if shorts["drafts"] < 1:
        raise ConfigError(
            f"{path}: shorts.drafts must be 1 or more (1 turns drafts off), "
            f"got {shorts['drafts']!r}."
        )

    rend = cfg["rendering"]
    for key in ("fps", "max_retries", "parallel_sections"):
        rend[key] = _as(path, f"rendering.{key}", rend[key], int, "an integer")
    if rend["parallel_sections"] < 1:
        raise ConfigError(
            f"{path}: rendering.parallel_sections must be 1 or more, "
            f"got {rend['parallel_sections']!r}."
        )
    segs = str(rend["parallel_segments"]).strip().lower()
    if segs != "auto":
        try:
            ok = int(segs) >= 1
        except ValueError:
            ok = False
        if not ok:
            raise ConfigError(
                f"{path}: rendering.parallel_segments must be 'auto' or 1 or more, "
                f"got {rend['parallel_segments']!r}."
            )
    rend["parallel_segments"] = segs
    for key in ("render_timeout_2d", "render_timeout_3d", "render_timeout_fallback"):
        rend[key] = _as(path, f"rendering.{key}", rend[key], float, "a number")

    quality = str(rend.get("quality", "")).strip().lower()
    if quality not in RENDER_QUALITY_FLAGS:
        raise ConfigError(
            f"{path}: rendering.quality {rend.get('quality')!r} is not valid; "
            f"use one of {sorted(RENDER_QUALITY_FLAGS)} "
            "(l = 480p, m = 720p, hd = 1080p, uhd = 4K)."
        )

    if not isinstance(cfg["tts"]["enabled"], bool):
        raise ConfigError(
            f"{path}: tts.enabled must be true or false, got {cfg['tts']['enabled']!r}."
        )
    _validate_tts(path, cfg["tts"])
    return cfg


def _validate_tts(path: Path, tts: dict) -> None:
    """Check the engine names and the nested offline-voice block."""
    engine = str(tts.get("engine") or "edge-tts").strip().lower()
    if engine not in TTS_ENGINES:
        raise ConfigError(
            f"{path}: tts.engine {tts.get('engine')!r} is not valid; "
            f"use one of {list(TTS_ENGINES)}."
        )
    tts["engine"] = engine
    fallback = str(tts.get("fallback_engine") or "none").strip().lower()
    if fallback not in TTS_FALLBACK_ENGINES:
        raise ConfigError(
            f"{path}: tts.fallback_engine {tts.get('fallback_engine')!r} is not "
            f"valid; use one of {list(TTS_FALLBACK_ENGINES)}."
        )
    tts["fallback_engine"] = fallback

    raw = tts.get("kokoro")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 'tts.kokoro' must be a block of settings.")
    kokoro = dict(DEFAULTS["tts"]["kokoro"])
    kokoro.update(raw)
    voice = str(kokoro["voice"] or "").strip()
    if not voice:
        raise ConfigError(f"{path}: tts.kokoro.voice must be a voice name.")
    kokoro["voice"] = voice
    kokoro["speed"] = _as(path, "tts.kokoro.speed", kokoro["speed"], float, "a number")
    if not 0.5 <= kokoro["speed"] <= 2.0:
        raise ConfigError(
            f"{path}: tts.kokoro.speed must be between 0.5 and 2.0, "
            f"got {kokoro['speed']!r}."
        )
    model_dir = kokoro.get("model_dir")
    if model_dir is not None:
        if not isinstance(model_dir, str) or not model_dir.strip():
            raise ConfigError(
                f"{path}: tts.kokoro.model_dir must be a folder path, "
                f"got {model_dir!r}."
            )
        kokoro["model_dir"] = str(Path(model_dir).expanduser())
    tts["kokoro"] = kokoro


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
