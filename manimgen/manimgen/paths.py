# Central output path resolver — reads from config.yaml, falls back to defaults.
#
# All pipeline modules import from here instead of hardcoding strings.
# Override any path by editing the output: block in config.yaml.

import os
import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

_DEFAULTS = {
    "scenes":  "manimgen/output/scenes",
    "videos":  "manimgen/output/videos",
    "logs":    "manimgen/output/logs",
    "audio":   "manimgen/output/audio",
    "muxed":   "manimgen/output/muxed",
    "exports": "manimgen/output/videos/exports",
    "plan":    "manimgen/output/plan.json",
}

_RENDER_DEFAULTS = {
    "quality":     "hd",
    "resolution":  "1920x1080",
    "fps":         30,
    "max_retries": 1,
}


def _load() -> tuple[dict, dict]:
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        out = cfg.get("output", {})
        rend = cfg.get("rendering", {})
        paths = {
            "scenes":  out.get("scenes_dir",  _DEFAULTS["scenes"]),
            "videos":  out.get("videos_dir",  _DEFAULTS["videos"]),
            "logs":    out.get("logs_dir",    _DEFAULTS["logs"]),
            "audio":   out.get("audio_dir",   _DEFAULTS["audio"]),
            "muxed":   out.get("muxed_dir",   _DEFAULTS["muxed"]),
            "exports": out.get("exports_dir", _DEFAULTS["exports"]),
            "plan":    out.get("plan_cache",  _DEFAULTS["plan"]),
        }
        rendering = {
            "quality":     rend.get("quality",     _RENDER_DEFAULTS["quality"]),
            "resolution":  rend.get("resolution",  _RENDER_DEFAULTS["resolution"]),
            "fps":         int(rend.get("fps",     _RENDER_DEFAULTS["fps"])),
            "max_retries": int(rend.get("max_retries", _RENDER_DEFAULTS["max_retries"])),
        }
        return paths, rendering
    except Exception:
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

def render_quality_flag() -> str:
    """Return the manimgl CLI flag for the configured quality, e.g. '--hd'."""
    q = _RENDERING["quality"]
    return f"--{q}"

def render_resolution() -> str:
    """Return resolution string, e.g. '1920x1080'."""
    return _RENDERING["resolution"]

def render_fps() -> int:
    return _RENDERING["fps"]

def render_max_retries() -> int:
    return _RENDERING["max_retries"]
