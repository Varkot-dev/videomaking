# Central output path resolver. Settings come from manimgen.config; output
# folders are absolute, anchored at the folder holding config.yaml.
#
# All pipeline modules import from here instead of hardcoding strings.
# Override any path by editing the output: block in config.yaml.

import os

from manimgen import config

_RENDER_DEFAULTS = config.DEFAULTS["rendering"]


def _load() -> tuple[dict, dict]:
    """Output folders (absolute, anchored at config.yaml) and render settings."""
    cfg = config.load()
    out = cfg["output"]
    paths = {
        "scenes": out["scenes_dir"],
        "videos": out["videos_dir"],
        "logs": out["logs_dir"],
        "audio": out["audio_dir"],
        "muxed": out["muxed_dir"],
        "exports": out["exports_dir"],
        "plan": out["plan_cache"],
    }
    return paths, dict(cfg["rendering"])


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
