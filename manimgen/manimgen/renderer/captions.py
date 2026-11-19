"""Burned-in word captions for vertical Shorts.

Most Shorts are watched with the sound off. The narration step already gets a
start and end time for every spoken word (edge-tts WordBoundary), so captions
reuse those timestamps: no speech recognition.

Pipeline: word timestamps -> caption chunks (1 to 4 words, broken at
punctuation and pauses) -> an ASS subtitle file -> one ffmpeg encode that burns
it in with the ``subtitles`` filter (``drawtext`` when this ffmpeg was built
without libass).

Placement (frame 1080x1920)
---------------------------
YouTube's UI covers the bottom 480 px and the top 288 px, and the right edge
carries the action buttons. The caption sits in the lowest free region: its
bottom edge is ``shorts.captions.margin_bottom_px`` above the bottom of the
frame (default 488, just above the 480 px UI band), inside a 48 px left and
192 px right margin. The text is centred in that safe band.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from manimgen import config

logger = logging.getLogger(__name__)

# `ffmpeg -filters` only lists what was compiled in: a metadata call.
_FFMPEG_PROBE_TIMEOUT_SECONDS = 15
# One encode of a Short (at most a few minutes of 1080x1920). Seconds of budget
# per second of video, with a floor, like the assembler's crossfade encode.
_BURN_TIMEOUT_PER_SECOND = 20
_BURN_MIN_TIMEOUT_SECONDS = 300
_DURATION_PROBE_TIMEOUT_SECONDS = 15

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920

MAX_WORDS = 4
# A silence this long between two words starts a new caption.
PAUSE_SECONDS = 0.35
# A gap this short between two captions is bridged: the first stays up.
BRIDGE_SECONDS = 0.30
# Long words: do not stack four of them into one line wider than the band.
MAX_CHARS = 26

_STRONG_BREAK = ".!?;:\u2026"
_SOFT_BREAK = ",\u2014\u2013)"
_TRAILING = _STRONG_BREAK + _SOFT_BREAK + "\"'\u201d\u2019"


@dataclass
class Caption:
    text: str
    start: float  # seconds on the final video's timeline
    end: float


@dataclass
class CaptionStyle:
    margin_bottom_px: int = 488
    margin_left_px: int = 48
    margin_right_px: int = 192
    font_size_px: int = 64
    font: str = "Arial"

    @classmethod
    def from_config(cls) -> CaptionStyle:
        cfg = config.section("shorts")["captions"]
        return cls(
            margin_bottom_px=int(cfg["margin_bottom_px"]),
            margin_left_px=int(cfg["margin_left_px"]),
            margin_right_px=int(cfg["margin_right_px"]),
            font_size_px=int(cfg["font_size_px"]),
            font=str(cfg["font"]),
        )


def captions_enabled_in_config() -> bool:
    return bool(config.section("shorts")["captions"]["enabled"])


# ---------------------------------------------------------------------------
# Words -> chunks
# ---------------------------------------------------------------------------


def _key(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower())


def _with_punctuation(words: list, narration: str) -> list[str]:
    """The words' text with the punctuation that followed them in the narration.

    WordBoundary tokens usually carry no punctuation, but the narration text
    does; walking the two in step recovers where the sentences end. A word the
    narration does not contain keeps its own text.
    """
    tokens = narration.split()
    out: list[str] = []
    pos = 0
    for w in words:
        text = str(getattr(w, "word", w))
        k = _key(text)
        found = None
        for j in range(pos, min(pos + 4, len(tokens))):
            if _key(tokens[j]) == k:
                found = j
                break
        if found is None or not k:
            out.append(text)
            continue
        tok = tokens[found]
        pos = found + 1
        trailing = ""
        for ch in reversed(tok):
            if ch in _TRAILING:
                trailing = ch + trailing
            else:
                break
        out.append(text if text.endswith(tuple(_TRAILING)) else text + trailing)
    return out


def chunk_words(
    words: list,
    narration: str | None = None,
    *,
    offset: float = 0.0,
    max_words: int = MAX_WORDS,
    pause: float = PAUSE_SECONDS,
    bridge: float = BRIDGE_SECONDS,
    max_chars: int = MAX_CHARS,
) -> list[Caption]:
    """Group timed words into captions of 1 to ``max_words`` words.

    ``words`` holds objects with ``word``, ``start`` and ``end`` (seconds from
    the audio start) or dicts with those keys. A caption breaks after
    sentence-ending punctuation or a comma, before a word that follows a pause
    of ``pause`` seconds or more, and when it reaches ``max_words`` words (or
    ``max_chars`` characters). It shows from its first word's start to its last
    word's end; when the next caption starts within ``bridge`` seconds, it stays
    up until then so the text does not flicker. ``offset`` moves everything to
    the section's start on the final video.
    """
    items = []
    for w in words:
        if isinstance(w, dict):
            items.append((str(w["word"]), float(w["start"]), float(w["end"])))
        else:
            items.append((str(w.word), float(w.start), float(w.end)))
    if not items:
        return []
    texts = (
        _with_punctuation(words, narration) if narration else [t for t, _, _ in items]
    )

    groups: list[list[int]] = []
    current: list[int] = []
    for i, (_, start, _end) in enumerate(items):
        if current:
            prev = current[-1]
            gap = start - items[prev][2]
            chars = sum(len(texts[k]) + 1 for k in current) + len(texts[i])
            if (
                len(current) >= max_words
                or gap >= pause
                or chars > max_chars
                or texts[prev].rstrip("\"'\u201d\u2019")[-1:] in _STRONG_BREAK
                or texts[prev].rstrip("\"'\u201d\u2019")[-1:] in _SOFT_BREAK
            ):
                groups.append(current)
                current = []
        current.append(i)
    if current:
        groups.append(current)

    captions = [
        Caption(
            text=" ".join(texts[k] for k in g).strip(),
            start=items[g[0]][1] + offset,
            end=items[g[-1]][2] + offset,
        )
        for g in groups
    ]
    captions = [c for c in captions if c.text]
    for a, b in zip(captions, captions[1:]):
        gap = b.start - a.end
        if 0 < gap < bridge:
            a.end = b.start
        elif gap < 0:  # never overlap
            a.end = b.start
    for c in captions:
        if c.end <= c.start:
            c.end = c.start + 0.1
    return captions


def section_offsets(durations: list[float]) -> list[float]:
    """Start time of each section on the assembled video.

    Sections are joined with a crossfade (the assembler's ``_xfade_chain_plan``):
    the next section starts at the fade's offset, which is earlier than the end
    of the previous one. One section starts at 0.
    """
    if not durations:
        return []
    if len(durations) == 1:
        return [0.0]
    from manimgen.renderer.assembler import _xfade_chain_plan

    plan = _xfade_chain_plan([max(0.01, float(d)) for d in durations])
    return [0.0] + [offset for _d, offset in plan]


def captions_for_sections(
    sections: list[tuple[list, str | None, float]],
) -> list[Caption]:
    """Captions for several sections: ``[(words, narration, audio_duration)]``."""
    offsets = section_offsets([d for _, _, d in sections])
    out: list[Caption] = []
    for off, (words, narration, _dur) in zip(offsets, sections):
        out.extend(chunk_words(words, narration, offset=off))
    return out


# ---------------------------------------------------------------------------
# ASS file
# ---------------------------------------------------------------------------


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _ass_text(text: str) -> str:
    # "{" opens an override block and "\" an escape in ASS text.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def build_ass(captions: list[Caption], style: CaptionStyle | None = None) -> str:
    """The ASS file text: bold sans, white with a dark outline, bottom centre.

    PlayRes is the 1080x1920 Shorts frame whatever the render size (a draft
    renders 540x960), so the margins are in frame pixels. Alignment 2 is bottom
    centre: MarginV is the distance from the frame's bottom edge to the text.
    """
    st = style or CaptionStyle.from_config()
    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {FRAME_WIDTH}\n"
        f"PlayResY: {FRAME_HEIGHT}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{st.font},{st.font_size_px},&H00FFFFFF,&H00FFFFFF,"
        "&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,6,0,"
        f"2,{st.margin_left_px},{st.margin_right_px},{st.margin_bottom_px},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Caption,,0,0,0,,"
        f"{_ass_text(c.text)}\n"
        for c in captions
    ]
    return head + "".join(lines)


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def escape_filter_path(path: str) -> str:
    """A file path as an ffmpeg filter option value (Windows included).

    Backslashes become forward slashes (ffmpeg accepts them on Windows) and the
    characters that are special in a filtergraph are escaped, so ``C:\\Users\\me``
    turns into ``C\\:/Users/me``. The value is meant to be wrapped in single
    quotes in the filter string.
    """
    p = path.replace("\\", "/")
    for ch in (":", "'", "[", "]", ",", ";", "="):
        p = p.replace(ch, "\\" + ch)
    return p


@functools.lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    """Filter names this ffmpeg was built with ('' set when it cannot be run)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 3:
            names.add(parts[1])
    return frozenset(names)


def has_libass() -> bool:
    f = available_filters()
    return "subtitles" in f or "ass" in f


def _drawtext_escape(text: str) -> str:
    # Inside single quotes only ' cannot appear; % starts text expansion.
    return text.replace("\\", "").replace("'", "\u2019").replace("%", "\\%")


def build_drawtext_filter(
    captions: list[Caption], style: CaptionStyle | None = None
) -> str:
    """Fallback filter chain: one ``drawtext`` per caption, timed with enable."""
    st = style or CaptionStyle.from_config()
    band = FRAME_WIDTH - st.margin_left_px - st.margin_right_px
    parts = []
    for c in captions:
        parts.append(
            "drawtext="
            f"text='{_drawtext_escape(c.text)}':"
            f"fontsize={st.font_size_px}:fontcolor=white:"
            "bordercolor=0x101010:borderw=6:"
            f"x={st.margin_left_px}+({band}-text_w)/2:"
            f"y=h-{st.margin_bottom_px}-text_h:"
            f"enable='between(t,{c.start:.3f},{c.end:.3f})'"
        )
    return ",".join(parts)


def _probe_duration(path: str) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=_DURATION_PROBE_TIMEOUT_SECONDS,
        )
        return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


_FONT_CANDIDATES = (
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
    "Arial Bold",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


def _load_font(size: int):
    """A bold sans PIL font at ``size`` px: a known file, else PIL's default."""
    from PIL import ImageFont

    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1: fixed-size bitmap font
        return ImageFont.load_default()


def render_caption_png(
    text: str, path: str, band_width: int, font_px: int, outline_px: int
) -> tuple[int, int]:
    """Draw one caption (white, dark outline) onto a transparent PNG.

    The image is ``band_width`` wide with the text centred; its bottom edge is
    the text's bottom edge. Returns the image size. A line wider than the band
    is shrunk to fit.
    """
    from PIL import Image, ImageDraw

    size = font_px
    while True:
        font = _load_font(size)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        width = probe.textlength(text, font=font)
        if width + 2 * outline_px <= band_width or size <= 12:
            break
        size -= 2
    height = int(size * 1.4) + 2 * outline_px
    img = Image.new("RGBA", (band_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        descent = font.getmetrics()[1]
    except AttributeError:
        descent = 0
    draw.text(
        (band_width / 2, height - outline_px - descent),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=outline_px,
        stroke_fill=(16, 16, 16, 255),
        anchor="ms",
    )
    img.save(path)
    return band_width, height


def _probe_size(path: str) -> tuple[int, int]:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=_DURATION_PROBE_TIMEOUT_SECONDS,
        )
        w, h = proc.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (OSError, ValueError, subprocess.SubprocessError):
        return FRAME_WIDTH, FRAME_HEIGHT


def build_overlay_inputs(
    captions: list[Caption],
    st: CaptionStyle,
    work: str,
    video_size: tuple[int, int],
) -> tuple[list[str], str]:
    """PNG-overlay burn: (extra ffmpeg input args, filter_complex string).

    Works on any ffmpeg (``overlay`` is always built in). The PNGs are drawn at
    the video's real size, with the ASS style's margins scaled from the
    1080x1920 frame, one ``overlay`` per caption enabled for its time span.
    """
    vw, vh = video_size
    k = vw / FRAME_WIDTH
    band = max(1, int((FRAME_WIDTH - st.margin_left_px - st.margin_right_px) * k))
    x = int(st.margin_left_px * k)
    bottom = int(st.margin_bottom_px * k)
    font_px = max(12, int(st.font_size_px * k))
    outline = max(1, int(6 * k))
    inputs: list[str] = []
    steps: list[str] = []
    prev = "0:v"
    for i, c in enumerate(captions):
        png = os.path.join(work, f"cap_{i:04d}.png")
        _w, h = render_caption_png(c.text, png, band, font_px, outline)
        inputs += ["-i", png]
        out = f"v{i + 1}"
        steps.append(
            f"[{prev}][{i + 1}:v]overlay=x={x}:y={vh - bottom - h}:"
            f"enable='between(t,{c.start:.3f},{c.end:.3f})'[{out}]"
        )
        prev = out
    return inputs, ";".join(steps)


def caption_method() -> str:
    """Best burn method this ffmpeg supports: ass, drawtext or overlay."""
    if has_libass():
        return "ass"
    if "drawtext" in available_filters():
        return "drawtext"
    return "overlay"


def burn_captions(
    video_path: str,
    captions: list[Caption],
    output_path: str | None = None,
    style: CaptionStyle | None = None,
    method: str | None = None,
) -> str:
    """Burn ``captions`` into the video with one encode; return the output path.

    ``method`` is ``ass`` (libass ``subtitles`` filter), ``drawtext`` or
    ``overlay`` (PNGs drawn with PIL, works on any ffmpeg); by default the
    first of those this ffmpeg supports. With no ``output_path`` the video is
    replaced in place (through a temp file next to it, so a failed encode
    leaves the original untouched). Raises RuntimeError when ffmpeg fails or
    times out.
    """
    if not captions:
        raise ValueError("burn_captions called with no captions")
    st = style or CaptionStyle.from_config()
    method = method or caption_method()
    out_dir = os.path.dirname(os.path.abspath(video_path))
    with tempfile.TemporaryDirectory(
        dir=out_dir, prefix="_cap_", ignore_cleanup_errors=True
    ) as work:
        extra_inputs: list[str] = []
        if method == "ass":
            ass_path = os.path.join(work, "captions.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(build_ass(captions, st))
            filt = ["-vf", f"subtitles=filename='{escape_filter_path(ass_path)}'"]
        elif method == "drawtext":
            logger.warning(
                "[captions] This ffmpeg has no libass; using drawtext (plainer)."
            )
            filt = ["-vf", build_drawtext_filter(captions, st)]
        elif method == "overlay":
            logger.info("[captions] No libass or drawtext; drawing captions as PNGs.")
            extra_inputs, graph = build_overlay_inputs(
                captions, st, work, _probe_size(video_path)
            )
            filt = ["-filter_complex", graph, "-map", f"[v{len(captions)}]"]
            filt += ["-map", "0:a?"]
        else:
            raise ValueError(f"unknown caption method {method!r}")
        tmp_out = os.path.join(work, "captioned.mp4")
        duration = _probe_duration(video_path)
        timeout = max(_BURN_MIN_TIMEOUT_SECONDS, _BURN_TIMEOUT_PER_SECOND * duration)
        cmd = ["ffmpeg", "-y", "-i", video_path, *extra_inputs, *filt]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            tmp_out,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"caption burn timed out after {timeout:.0f}s") from e
        except OSError as e:
            raise RuntimeError(f"could not run ffmpeg: {e}") from e
        if proc.returncode != 0 or not os.path.exists(tmp_out):
            raise RuntimeError(
                "ffmpeg failed to burn captions: " + (proc.stderr or "")[-400:]
            )
        dest = output_path or video_path
        os.replace(tmp_out, dest)
    return dest


def add_captions(
    video_path: str,
    sections: list[tuple[list, str | None, float]],
) -> bool:
    """Caption the final video from ``[(words, narration, audio_duration)]``.

    Returns True when captions were burned in. Skips with a log line, leaving
    the video untouched, when there are no word timestamps (narration off or
    silent) and when the burn fails: a Short without captions beats no Short.
    """
    if not any(words for words, _, _ in sections):
        logger.info(
            "[captions] Skipped: no word timestamps (narration is off or silent)."
        )
        return False
    captions = captions_for_sections(sections)
    if not captions:
        logger.info("[captions] Skipped: no caption text.")
        return False
    try:
        burn_captions(video_path, captions)
    except (RuntimeError, ValueError) as e:
        logger.warning("[captions] Not burned in, video left without captions: %s", e)
        return False
    logger.info("[captions] Burned %d captions into %s", len(captions), video_path)
    return True
