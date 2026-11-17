# Central output path resolver. Settings come from manimgen.config; output
# folders are absolute, anchored at the folder holding config.yaml.
#
# All pipeline modules import from here instead of hardcoding strings.
# Override any path by editing the output: block in config.yaml.

import os

from manimgen import config

_RENDER_DEFAULTS = config.DEFAULTS["rendering"]


def _load() -> tuple[dict, dict, dict]:
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
    return paths, dict(cfg["rendering"]), dict(cfg["shorts"])


_PATHS, _RENDERING, _SHORTS = _load()


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


# The one table of quality names lives in config.py, which also validates the
# configured value when config.yaml is loaded.
_QUALITY_FLAGS = config.RENDER_QUALITY_FLAGS


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


# Output formats. "landscape" is the configured long-form video; "short" is a
# vertical 9:16 video for YouTube Shorts. The format is chosen once per run
# (cli --short, or the plan being resumed) before anything renders.
SHORT_RESOLUTION = "1080x1920"
_VIDEO_FORMATS = ("landscape", "short")
_video_format = "landscape"


def set_video_format(fmt: str) -> None:
    """Select the output format for this run ("landscape" or "short")."""
    global _video_format
    if fmt not in _VIDEO_FORMATS:
        raise ValueError(f"unknown video format {fmt!r}; use one of {_VIDEO_FORMATS}")
    _video_format = fmt


def video_format() -> str:
    return _video_format


def is_short() -> bool:
    return _video_format == "short"


def render_resolution() -> str:
    """Return resolution string, e.g. '1920x1080' (1080x1920 for a short)."""
    if is_short():
        return SHORT_RESOLUTION
    return _RENDERING["resolution"]


def render_size_flags() -> list[str]:
    """manimgl flags that set the frame size.

    A short passes an explicit ``-r 1080x1920``: manimgl derives the frame
    (4.5 x 8 units) from the resolution, and its quality flags (-l/-m/--hd)
    are landscape presets that would override it, so they are never combined.
    """
    if is_short():
        return ["-r", SHORT_RESOLUTION]
    return [render_quality_flag()]


def short_voice_profile_path() -> str:
    """Where the optional voice profile for shorts lives (may not exist)."""
    return _SHORTS["voice_profile"]


def render_fps() -> int:
    return _RENDERING["fps"]


def render_max_retries() -> int:
    return _RENDERING["max_retries"]


def render_parallel_sections() -> int:
    """How many sections to generate and render at once (at least 1).

    ``MANIMGEN_PARALLEL_SECTIONS`` overrides config.yaml for one run.
    """
    raw = os.environ.get("MANIMGEN_PARALLEL_SECTIONS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, int(_RENDERING.get("parallel_sections") or 1))
