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


# Draft mode (cli --draft): render at half size and at draft_fps, for a quick
# look or a test run. Landscape drops to 960x540, a short to 540x960 (a quarter
# of the pixels either way). Everything downstream
# (cutter, muxer, assembler, captions) follows render_resolution() and
# render_fps().
_DRAFT_SHORT_RESOLUTION = "540x960"
_draft = False


def set_draft(enabled: bool) -> None:
    global _draft
    _draft = bool(enabled)


def is_draft() -> bool:
    return _draft


def _use_draft(draft: bool | None) -> bool:
    """An explicit per-call request wins over the process-wide draft switch."""
    return _draft if draft is None else bool(draft)


def draft_active(draft: bool | None = None) -> bool:
    """True when this call renders at the draft size and frame rate."""
    return _use_draft(draft)


def render_quality_flag() -> str:
    """Return the manimgl CLI flag for the configured quality, e.g. '--hd'.

    A draft does not use a quality flag (see ``render_size_flags``).
    """
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


def preflight_enabled() -> bool:
    """Whether a scene is dry-run for layout problems before its real render.

    ``rendering.preflight`` in config.yaml (default on); ``MANIMGEN_PREFLIGHT=0``
    (or ``false``/``off``) turns it off for one run, any other non-empty value
    turns it on. With it off the pipeline behaves as it did without preflight.
    """
    raw = os.environ.get("MANIMGEN_PREFLIGHT", "").strip().lower()
    if raw:
        return raw not in ("0", "false", "off", "no")
    value = _RENDERING.get("preflight", _RENDER_DEFAULTS["preflight"])
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no", "")
    return bool(value)


def preflight_timeout() -> float:
    """Seconds a preflight dry run may take before its process tree is killed."""
    value = float(
        _RENDERING.get("preflight_timeout") or _RENDER_DEFAULTS["preflight_timeout"]
    )
    return value if value > 0 else float(_RENDER_DEFAULTS["preflight_timeout"])


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


def render_resolution(draft: bool | None = None) -> str:
    """Return resolution string, e.g. '1920x1080' (1080x1920 for a short)."""
    small = _use_draft(draft)
    if is_short():
        return _DRAFT_SHORT_RESOLUTION if small else SHORT_RESOLUTION
    if not small:
        return _RENDERING["resolution"]
    w, h = (int(n) for n in _RENDERING["resolution"].lower().split("x"))
    return f"{w // 2}x{h // 2}"


def render_size_flags(draft: bool | None = None) -> list[str]:
    """manimgl flags that set the frame size.

    A short passes an explicit ``-r 1080x1920``: manimgl derives the frame
    (4.5 x 8 units) from the resolution, and its quality flags (-l/-m/--hd)
    are landscape presets that would override it, so they are never combined.

    ``draft=True`` gives the draft size for this call only, without touching
    the process-wide switch, so sections rendering in parallel threads are
    not affected.
    """
    if is_short() or _use_draft(draft):
        return ["-r", render_resolution(draft)]
    return [render_quality_flag()]


# The frame rate manimgl renders at when no config file sets one (its
# default_config.yml). A final render passes none, so this is its real rate.
MANIMGL_DEFAULT_FPS = 30


def native_render_fps(draft: bool | None = None) -> int:
    """Frame rate manimgl itself renders at for this call.

    A draft sets ``rendering.draft_fps``; a final leaves manimgl's own default,
    which the cutter or assembler later lifts to ``rendering.fps``.
    """
    if _use_draft(draft):
        return render_fps(draft)
    return MANIMGL_DEFAULT_FPS


# Strips of a 1080x1920 frame the Shorts app draws over, as fractions of the
# frame: 48 px left, 480 px bottom (title, channel), 192 px right (buttons),
# 288 px top. No official figure exists. Google's vertical ads template
# reserves 672 px at the bottom (it adds ad buttons); measurements of organic
# Shorts put the covered strip at 300 to 390 px. 480 px sits between them and
# leaves the diagram room to be read on a phone. Confirm with a test upload.
MAX_SHORT_DRAFTS = 3

_SHORT_SAFE_MARGINS = (48 / 1080, 480 / 1920, 192 / 1080, 288 / 1920)


def short_safe_margins() -> tuple[float, float, float, float]:
    """(left, bottom, right, top) margins of the safe area in a short."""
    return _SHORT_SAFE_MARGINS


def short_drafts() -> int:
    """How many scenes the Director writes for a short (1 means no drafts).

    ``MANIMGEN_SHORT_DRAFTS`` overrides ``shorts.drafts`` in config.yaml for
    one run. An unparsable or non-positive value falls back to the config.
    The count is capped at 3: each extra draft costs another scene-writing
    call and another small render.
    """
    raw = os.environ.get("MANIMGEN_SHORT_DRAFTS", "").strip()
    if raw:
        try:
            value = int(raw)
            if value >= 1:
                return min(value, MAX_SHORT_DRAFTS)
        except ValueError:
            pass
    configured = int(_SHORTS.get("drafts") or config.DEFAULTS["shorts"]["drafts"])
    return max(1, min(configured, MAX_SHORT_DRAFTS))


def short_voice_profile_path() -> str:
    """Where the optional voice profile for shorts lives (may not exist)."""
    return _SHORTS["voice_profile"]


def render_fps(draft: bool | None = None) -> int:
    """Frame rate of the render and of every clip cut from it.

    A draft (this call's ``draft``, else the process-wide switch) uses
    ``rendering.draft_fps``; a final uses ``rendering.fps``.
    """
    if _use_draft(draft):
        return int(_RENDERING.get("draft_fps") or _RENDER_DEFAULTS["draft_fps"])
    return _RENDERING["fps"]


def render_max_retries() -> int:
    return _RENDERING["max_retries"]


def render_parallel_segments() -> int | None:
    """Segments one scene is rendered in at once: None for "auto", else 1 or more.

    ``MANIMGEN_PARALLEL_SEGMENTS`` overrides ``rendering.parallel_segments``;
    1 renders in one process, as before. See ``validator/segmented_render.py``.
    """
    for raw in (
        os.environ.get("MANIMGEN_PARALLEL_SEGMENTS", ""),
        str(_RENDERING.get("parallel_segments", "auto")),
    ):
        raw = raw.strip().lower()
        if raw in ("", "auto"):
            if raw == "auto":
                return None
            continue
        try:
            return max(1, int(raw))
        except ValueError:
            continue
    return None


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
