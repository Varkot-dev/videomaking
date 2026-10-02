# Central output path resolver — reads from config.yaml, falls back to defaults.
#
# All pipeline modules import from here instead of hardcoding strings.
# Override any path by editing the output: block in config.yaml.

import os
import warnings

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

_DEFAULTS = {
    "scenes": "manimgen/output/scenes",
    "videos": "manimgen/output/videos",
    "logs": "manimgen/output/logs",
    "audio": "manimgen/output/audio",
    "muxed": "manimgen/output/muxed",
    "exports": "manimgen/output/videos/exports",
    "plan": "manimgen/output/plan.json",
}

_RENDER_DEFAULTS = {
    "quality": "hd",
    "resolution": "1920x1080",
    "fps": 60,
    "max_retries": 1,
    # Render time budgets in seconds. Not changed until measured on the target
    # PC (scripts/render_smoke.py); only made configurable.
    "render_timeout_2d": 240,
    "render_timeout_3d": 360,
    "render_timeout_fallback": 180,
}


def _load() -> tuple[dict, dict]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        out = cfg.get("output", {})
        rend = cfg.get("rendering", {})
        paths = {
            "scenes": out.get("scenes_dir", _DEFAULTS["scenes"]),
            "videos": out.get("videos_dir", _DEFAULTS["videos"]),
            "logs": out.get("logs_dir", _DEFAULTS["logs"]),
            "audio": out.get("audio_dir", _DEFAULTS["audio"]),
            "muxed": out.get("muxed_dir", _DEFAULTS["muxed"]),
            "exports": out.get("exports_dir", _DEFAULTS["exports"]),
            "plan": out.get("plan_cache", _DEFAULTS["plan"]),
        }
        rendering = {
            "quality": rend.get("quality", _RENDER_DEFAULTS["quality"]),
            "resolution": rend.get("resolution", _RENDER_DEFAULTS["resolution"]),
            "fps": int(rend.get("fps", _RENDER_DEFAULTS["fps"])),
            "max_retries": int(
                rend.get("max_retries", _RENDER_DEFAULTS["max_retries"])
            ),
            "render_timeout_2d": float(
                rend.get("render_timeout_2d", _RENDER_DEFAULTS["render_timeout_2d"])
            ),
            "render_timeout_3d": float(
                rend.get("render_timeout_3d", _RENDER_DEFAULTS["render_timeout_3d"])
            ),
            "render_timeout_fallback": float(
                rend.get(
                    "render_timeout_fallback",
                    _RENDER_DEFAULTS["render_timeout_fallback"],
                )
            ),
        }
        return paths, rendering
    except Exception as e:
        # Runs at import time before logging is configured, so warn() instead
        # of logging. A malformed config silently routing to default output
        # dirs is exactly the failure this surfaces.
        warnings.warn(
            f"Failed to load config {_CONFIG_PATH} ({e}) — "
            f"falling back to default output paths and rendering settings",
            RuntimeWarning,
            stacklevel=2,
        )
        return dict(_DEFAULTS), dict(_RENDER_DEFAULTS)


_PATHS, _RENDERING = _load()


def scenes_dir() -> str:
    return _PATHS["scenes"]


def videos_dir() -> str:
    return _PATHS["videos"]


def logs_dir() -> str:
    return _PATHS["logs"]


def audio_dir() -> str:
    return _PATHS["audio"]


def muxed_dir() -> str:
    return _PATHS["muxed"]


def exports_dir() -> str:
    return _PATHS["exports"]


def plan_cache() -> str:
    return _PATHS["plan"]


# ---------------------------------------------------------------------------
# Rendering config
# ---------------------------------------------------------------------------


# manimgl accepts exactly these quality flags (`manimgl --help`): -l is 480p,
# -m is 720p, --hd is 1080p, --uhd is 4K. The configured name is mapped to one
# of them; building "--" + name produced invalid flags such as "--l".
_QUALITY_FLAGS = {
    "l": "-l",
    "low": "-l",
    "m": "-m",
    "medium": "-m",
    "hd": "--hd",
    "high": "--hd",
    "uhd": "--uhd",
    "4k": "--uhd",
}


def render_quality_flag() -> str:
    """Return the manimgl CLI flag for the configured quality, e.g. '--hd'."""
    q = str(_RENDERING["quality"]).strip().lower()
    try:
        return _QUALITY_FLAGS[q]
    except KeyError:
        raise ValueError(
            f"rendering.quality {q!r} in config.yaml is not valid; use one of "
            f"{sorted(_QUALITY_FLAGS)} (l = 480p, m = 720p, hd = 1080p, uhd = 4K)"
        ) from None


def render_timeout(kind: str) -> float:
    """Seconds a manimgl render may run before its process tree is killed.

    ``kind`` is "2d", "3d" or "fallback". Defaults come from config.yaml
    ``rendering.render_timeout_*``; the environment variable
    ``MANIMGEN_RENDER_TIMEOUT_<KIND>`` (e.g. ``MANIMGEN_RENDER_TIMEOUT_2D``)
    overrides it. A non-positive or unparsable value falls back to the config.
    """
    key = f"render_timeout_{kind}"
    raw = os.environ.get(f"MANIMGEN_RENDER_TIMEOUT_{kind.upper()}")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    value = float(_RENDERING.get(key) or _RENDER_DEFAULTS[key])
    return value if value > 0 else float(_RENDER_DEFAULTS[key])


def render_resolution() -> str:
    """Return resolution string, e.g. '1920x1080'."""
    return _RENDERING["resolution"]


def render_fps() -> int:
    return _RENDERING["fps"]


def render_max_retries() -> int:
    return _RENDERING["max_retries"]
