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


def _load() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        out = cfg.get("output", {})
        return {
            "scenes":  out.get("scenes_dir",  _DEFAULTS["scenes"]),
            "videos":  out.get("videos_dir",  _DEFAULTS["videos"]),
            "logs":    out.get("logs_dir",    _DEFAULTS["logs"]),
            "audio":   out.get("audio_dir",   _DEFAULTS["audio"]),
            "muxed":   out.get("muxed_dir",   _DEFAULTS["muxed"]),
            "exports": out.get("exports_dir", _DEFAULTS["exports"]),
            "plan":    out.get("plan_cache",  _DEFAULTS["plan"]),
        }
    except Exception:
        return dict(_DEFAULTS)


_PATHS = _load()


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
