"""Render-side text overlap probe (#97).

Runs INSIDE the manimgl child process, where every mobject's geometry is
known, and records text drawn on top of other text. Zero LLM cost: it is plain
bounding-box arithmetic on the scene's own mobjects.

How it gets there: ``render_command.run_manimgl`` puts the private
``bootstrap/`` directory (which holds a tiny ``sitecustomize.py``) on the
child's PYTHONPATH and names a JSON report file in ``MANIMGEN_OVERLAP_REPORT``.
Python imports ``sitecustomize`` at start-up; it loads this file by path and
calls :func:`install`, which waits for ``manimlib.scene.scene`` to be imported
(a ``sys.meta_path`` hook, so manimgl's own start-up and argument parsing are
untouched) and then wraps ``Scene.post_play`` and ``Scene.tear_down``. The
generated scene file is never modified, so the scene safety gate still sees
exactly what the LLM wrote.

``post_play`` runs at the end of every ``play()`` and ``wait()``: the mobjects
are then in a stable state, not mid-transition. At each such point the probe
collects visible text-like mobjects and compares their bounding boxes pairwise.
The report is written when the scene tears down (and again at exit, in case
tear-down never ran).

Self-contained on purpose: stdlib only (manimlib objects are handled through
duck typing and class names), so it does not import the manimgen package in
the render child. It must never break a render: when the env var is unset it
does nothing, and any failure inside it is caught and recorded as
``probe_error`` in the report.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from dataclasses import dataclass, field

REPORT_ENV = "MANIMGEN_OVERLAP_REPORT"
REPORT_VERSION = 1
# Set by the host for a segment or planning render when the overlap probe is
# switched off: the child still installs this module (it also keeps skipped
# animations faithful, see ``_patch_skipping``) and records ``play_times``, but
# it neither observes, nudges nor reports findings.
PASSIVE_ENV = "MANIMGEN_PROBE_PASSIVE"
MAX_PLAY_TIMES = 20000

# Intersection area as a fraction of the SMALLER box. Calibrated on the first
# real end-to-end run (#97) and the hand-verified examples: see
# tests/fixtures/overlap/CALIBRATION.md for the numbers.
OVERLAP_THRESHOLD = 0.2
# Intersections smaller than this (scene units squared) are ignored: glyph
# boxes of neighbouring lines can graze each other by a hair.
MIN_OVERLAP_AREA = 0.02
# A text whose most opaque part is at or below this is treated as invisible.
MIN_OPACITY = 0.05
# morph_collision() score above which a TransformMatchingTex midpoint would be
# reported as a finding. None: scores are recorded in the report ("morphs") as
# a diagnostic only. On the first real run every TransformMatchingTex between
# dissimilar equations looked garbled at its midpoint (scores 0.03 to 0.40), so
# there is no threshold that flags "the bad one" without flagging ordinary
# morphs; see tests/fixtures/overlap/CALIBRATION.md.
MORPH_THRESHOLD: float | None = None
MAX_FINDINGS = 10
# Share of a text's box that must be outside the frame, or outside the safe
# area of a vertical short, before it is reported. Small overshoots (a
# descender, a stroke) are ignored.
OFFSCREEN_THRESHOLD = 0.05
UNSAFE_THRESHOLD = 0.15
# "left,bottom,right,top" as fractions of the frame: the strips the Shorts app
# covers. Set by the host only when rendering a vertical short.
SAFE_MARGINS_ENV = "MANIMGEN_SAFE_MARGINS"
# Safe-area nudge (vertical shorts only, i.e. when safe margins are set): text
# that is about to appear slightly outside the safe area is moved inside before
# its first frame. Only a text-only mobject that is new to the scene and fits
# the safe area is moved, and only by up to NUDGE_LIMIT units; anything further
# out is a layout mistake the probe reports for a real fix.
NUDGE_ENV = "MANIMGEN_SAFE_NUDGE"
# Set to "1" by render_command for a dry run (manimgl -s). Must match
# render_command.DRY_RUN_ENV.
DRY_RUN_ENV = "MANIMGEN_DRY_RUN"
NUDGE_LIMIT = 0.5
MAX_PER_TEXT = 3  # findings that may name the same string
# "small": rendered text height as a share of what the same text gives at
# font_size 22 (the smallest size that reads on a phone). Below this is small.
# 22 renders at exactly 1.0; 0.9 leaves room for rounding and for a kit width
# guard that shrinks a 28 a little.
SMALL_RATIO = 0.9
MIN_READABLE_FONT = 22
MAX_REFERENCES = 40  # distinct texts a reference size is measured for
# "on_shape": a text sitting on a drawn outline or solid fill. Share of the
# text's longest side that an outline runs through (a line through the middle
# of a label), and share of the text covered by a mostly opaque light fill.
ON_SHAPE_CROSS = 0.25
ON_SHAPE_FILL_OPACITY = 0.5
ON_SHAPE_FILL_SHARE = 0.5
DARK_FILL = 0.15  # fills darker than this (relative luminance) are backing plates
MAX_SHAPES = 400
TEXT_CHARS = 40

# Class names (anywhere in the MRO) that make a mobject text-like in manimgl
# 1.7.2: StringMobject covers Text, MarkupText, Code, Tex and TexText;
# DecimalNumber covers Integer; SingleStringTex covers OldTex/OldTexText.
_TEXT_BASES = frozenset({"StringMobject", "DecimalNumber", "SingleStringTex"})
# Tex subclasses that are shapes, not text.
_NOT_TEXT = frozenset({"Brace", "Checkmark", "Exmark"})


@dataclass
class TextItem:
    """One visible text-like mobject at one stable moment."""

    text: str
    box: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    kind: str = "Text"
    # ids of every ancestor, outermost first. Used for ancestor/descendant
    # exclusion; ancestors[0] is the top-level scene mobject.
    ancestors: tuple[int, ...] = ()
    ident: int = 0
    # The visible rectangle this text is measured against (the camera frame for
    # world-space text, the default frame for fixed-in-frame text).
    rect: tuple[float, float, float, float] | None = None
    obj: object = field(default=None, repr=False, compare=False)


@dataclass
class ShapeItem:
    """One visible non-text drawn part (a stroke or a fill) at one moment."""

    name: str
    box: tuple[float, float, float, float]
    polys: list  # outline polylines: lists of (x, y)
    fill: float  # fill opacity
    dark: bool  # fill colour is close to the background


@dataclass
class Finding:
    time: float
    a: str
    b: str
    fraction: float
    a_kind: str = "Text"
    b_kind: str = "Text"
    # "overlap": a drawn over b. "offscreen": a is cut off by the frame edge.
    # "unsafe": a sits under the app's buttons or caption (vertical shorts).
    # "small": a is rendered below font_size 22 (fraction is its size relative
    # to 22). "on_shape": a sits on the outline or fill of shape b.
    kind: str = "overlap"

    def as_dict(self) -> dict:
        return {
            "time": round(self.time, 2),
            "a": self.a,
            "b": self.b,
            "fraction": round(self.fraction, 2),
            "a_kind": self.a_kind,
            "b_kind": self.b_kind,
            "kind": self.kind,
        }


# ---------------------------------------------------------------------------
# Geometry (pure, unit-tested without manimlib)
# ---------------------------------------------------------------------------


def _area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h


def overlap_fraction(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Intersection area as a fraction of the smaller box (0.0 to 1.0)."""
    smaller = min(_area(a), _area(b))
    if smaller <= 0:
        return 0.0
    return intersection_area(a, b) / smaller


def outside_fraction(
    box: tuple[float, float, float, float], rect: tuple[float, float, float, float]
) -> float:
    """Share of ``box`` that lies outside ``rect`` (0.0 inside, 1.0 fully out)."""
    area = _area(box)
    if area <= 0:
        return 0.0
    return max(0.0, 1.0 - intersection_area(box, rect) / area)


def inset_rect(
    rect: tuple[float, float, float, float],
    margins: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """``rect`` shrunk by ``margins`` = (left, bottom, right, top), each a
    fraction of the rect's width or height. Scales with the camera frame."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    left, bottom, right, top = margins
    return (
        rect[0] + left * w,
        rect[1] + bottom * h,
        rect[2] - right * w,
        rect[3] - top * h,
    )


def segment_length_in_box(p, q, box) -> float:
    """Length of segment p-q inside ``box`` (Liang-Barsky)."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for num, den in (
        (-dx, x0 - box[0]),
        (dx, box[2] - x0),
        (-dy, y0 - box[1]),
        (dy, box[3] - y0),
    ):
        if num == 0:
            if den < 0:
                return 0.0
            continue
        r = den / num
        if num < 0:
            if r > t1:
                return 0.0
            t0 = max(t0, r)
        else:
            if r < t0:
                return 0.0
            t1 = min(t1, r)
    if t1 <= t0:
        return 0.0
    return (t1 - t0) * ((dx * dx + dy * dy) ** 0.5)


def outline_share(box, polys) -> float:
    """Outline length inside ``box`` relative to the box's SHORTEST side.

    A line that crosses a text scores about 1.0 however long the text is; a
    corner that only grazes it scores near 0.
    """
    inside = 0.0
    for poly in polys:
        for a, b in zip(poly, poly[1:]):
            inside += segment_length_in_box(a, b, box)
    shortest = min(box[2] - box[0], box[3] - box[1])
    return inside / shortest if shortest > 0 else 0.0


def point_in_polys(x, y, polys) -> bool:
    inside = False
    for poly in polys:
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def fill_cover(box, polys, n: int = 6) -> float:
    """Share of ``box`` lying inside the filled polygons (n x n samples)."""
    hits = 0
    for i in range(n):
        for j in range(n):
            x = box[0] + (i + 0.5) / n * (box[2] - box[0])
            y = box[1] + (j + 0.5) / n * (box[3] - box[1])
            if point_in_polys(x, y, polys):
                hits += 1
    return hits / (n * n)


def on_shape_raw(box, shape: "ShapeItem") -> tuple[float, float]:
    """(outline share, fill cover) of text ``box`` on ``shape``, before thresholds.

    The box is shrunk by 0.01 so a text that merely touches an outline is not
    counted. Cover is 0.0 unless the fill is mostly opaque and not a dark
    backing plate.
    """
    probe_box = (box[0] + 0.01, box[1] + 0.01, box[2] - 0.01, box[3] - 0.01)
    if intersection_area(probe_box, shape.box) <= 0:
        return 0.0, 0.0
    cross = outline_share(probe_box, shape.polys)
    cover = 0.0
    if shape.fill >= ON_SHAPE_FILL_OPACITY and not shape.dark:
        cover = fill_cover(probe_box, shape.polys)
    return cross, cover


def on_shape_score(box, shape: "ShapeItem") -> float:
    """How much text ``box`` sits on ``shape``: 0.0 when it does not.

    Two ways to be "on" a shape: an outline runs through the text (share of
    the text's longest side), or the text lies on a mostly opaque, light fill.
    Text inside a faint fill (a label centred in a 0.25-opacity square) or on a
    dark backing plate scores 0.
    """
    cross, cover = on_shape_raw(box, shape)
    score = cross if cross >= ON_SHAPE_CROSS else 0.0
    if cover >= ON_SHAPE_FILL_SHARE:
        score = max(score, cover)
    return min(score, 1.0)


def small_ratio(height: float, reference_height: float) -> float | None:
    """Rendered height as a share of the font_size-22 height; None if unknown."""
    if reference_height <= 0 or height <= 0:
        return None
    return height / reference_height


def parse_margins(raw: str | None) -> tuple[float, float, float, float] | None:
    """Parse "left,bottom,right,top" fractions; None when absent or invalid."""
    if not raw:
        return None
    try:
        values = tuple(float(v) for v in raw.split(","))
    except ValueError:
        return None
    if len(values) != 4 or any(v < 0 or v >= 0.5 for v in values):
        return None
    return values


def _normalize(text: str) -> str:
    return " ".join(text.split())


def shorten(text: str, limit: int = TEXT_CHARS) -> str:
    text = _normalize(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def overlapping_pairs(
    items: list[TextItem],
    threshold: float = OVERLAP_THRESHOLD,
    min_area: float = MIN_OVERLAP_AREA,
) -> list[tuple[TextItem, TextItem, float]]:
    """Pairs of items whose boxes overlap above ``threshold``.

    Skipped: one item is an ancestor of the other, both show the same string
    (a copy fading into its replacement), or the intersection is tiny.
    """
    found = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if a.ident in b.ancestors or b.ident in a.ancestors:
                continue
            if _normalize(a.text) == _normalize(b.text):
                continue
            if intersection_area(a.box, b.box) < min_area:
                continue
            frac = overlap_fraction(a.box, b.box)
            if frac >= threshold:
                found.append((a, b, frac))
    return found


def morph_collision(glyphs: list[tuple[int, tuple, float]]) -> float:
    """Share of glyph area drawn over a glyph from ANOTHER part of a morph.

    ``glyphs`` holds (part index, box, area) for every visible glyph at the
    midpoint of a TransformMatchingTex/TransformMatchingStrings. A glyph is
    "covered" by the largest fraction of its box that a glyph of a different
    part overlaps. The result is the area-weighted mean coverage: low for a
    clean morph, high when outgoing and incoming text pile up into a garble.
    """
    total = 0.0
    covered = 0.0
    for i, (part_i, box_i, area_i) in enumerate(glyphs):
        if area_i <= 0:
            continue
        best = 0.0
        for j, (part_j, box_j, _area_j) in enumerate(glyphs):
            if i == j or part_i == part_j:
                continue
            best = max(best, intersection_area(box_i, box_j) / area_i)
            if best >= 1.0:
                break
        total += area_i
        covered += area_i * min(best, 1.0)
    return covered / total if total > 0 else 0.0


@dataclass
class OverlapRecorder:
    """Accumulates de-duplicated findings across the stable states of a scene."""

    threshold: float = OVERLAP_THRESHOLD
    min_area: float = MIN_OVERLAP_AREA
    max_findings: int = MAX_FINDINGS
    findings: list[Finding] = field(default_factory=list)
    seen: set = field(default_factory=set)
    dropped: int = 0
    checks: int = 0
    max_fraction_below: float = 0.0  # largest fraction NOT reported (calibration)
    max_below_pair: tuple = ()
    max_cross_seen: float = 0.0  # largest outline share NOT reported (calibration)
    min_ratio_seen: float = 9.9  # smallest text size ratio vs 22 (calibration)
    morph_threshold: float | None = MORPH_THRESHOLD
    morphs: list = field(default_factory=list)  # every morph score (calibration)

    def observe(self, time: float, items: list[TextItem]) -> None:
        self.checks += 1
        for a, b, frac in overlapping_pairs(items, 0.0, self.min_area):
            if frac < self.threshold:
                if frac > self.max_fraction_below:
                    self.max_fraction_below = frac
                    self.max_below_pair = (shorten(a.text), shorten(b.text))
                continue
            self._add(
                Finding(time, shorten(a.text), shorten(b.text), frac, a.kind, b.kind)
            )

    def observe_bounds(
        self,
        time: float,
        items: list[TextItem],
        safe_margins: tuple[float, float, float, float] | None = None,
    ) -> None:
        """Report text cut off by the frame edge, and (when ``safe_margins`` is
        given, i.e. a vertical short) text under the app's buttons or caption.
        """
        for item in items:
            if item.rect is None:
                continue
            out = outside_fraction(item.box, item.rect)
            if out >= OFFSCREEN_THRESHOLD:
                self._add(
                    Finding(
                        time, shorten(item.text), "", out, item.kind, "", "offscreen"
                    )
                )
                continue
            if safe_margins is None:
                continue
            out = outside_fraction(item.box, inset_rect(item.rect, safe_margins))
            if out >= UNSAFE_THRESHOLD:
                self._add(
                    Finding(time, shorten(item.text), "", out, item.kind, "", "unsafe")
                )

    def observe_small(self, time: float, items: list[TextItem], reference) -> None:
        """Report visible text rendered below font_size 22.

        ``reference(item)`` returns the height the same text has at font_size
        22, or None when it cannot be measured.
        """
        for item in items:
            ref = reference(item)
            ratio = small_ratio(item.box[3] - item.box[1], ref or 0.0)
            if ratio is not None:
                self.min_ratio_seen = min(self.min_ratio_seen, ratio)
            if ratio is None or ratio >= SMALL_RATIO:
                continue
            self._add(
                Finding(time, shorten(item.text), "", ratio, item.kind, "", "small")
            )

    def observe_shapes(
        self, time: float, items: list[TextItem], shapes: list["ShapeItem"]
    ) -> None:
        """Report text sitting on the outline or solid fill of a drawn shape."""
        for item in items:
            best = None
            for shape in shapes:
                score = on_shape_score(item.box, shape)
                if score == 0:
                    self.max_cross_seen = max(
                        self.max_cross_seen, on_shape_raw(item.box, shape)[0]
                    )
                if score > 0 and (best is None or score > best[0]):
                    best = (score, shape)
            if best is not None:
                self._add(
                    Finding(
                        time,
                        shorten(item.text),
                        best[1].name,
                        best[0],
                        item.kind,
                        best[1].name,
                        "on_shape",
                    )
                )

    def observe_morph(self, time: float, source: str, target: str, glyphs) -> None:
        score = morph_collision(glyphs)
        self.morphs.append(
            {
                "time": round(time, 2),
                "source": shorten(source),
                "target": shorten(target),
                "collision": round(score, 3),
            }
        )
        if self.morph_threshold is None or score < self.morph_threshold:
            return
        self._add(
            Finding(time, shorten(source), shorten(target), score, "morph", "morph")
        )

    def _add(self, finding: Finding) -> None:
        key = (finding.kind, *sorted((finding.a, finding.b)))
        if key in self.seen:
            return
        self.seen.add(key)
        # One caption laid over a whole row of labels would otherwise fill the
        # list with the same defect and hide later ones.
        names = {finding.a, finding.b} - {""}
        per_text = sum(1 for f in self.findings if {f.a, f.b} & names)
        if len(self.findings) >= self.max_findings or per_text >= MAX_PER_TEXT:
            self.dropped += 1
            return
        self.findings.append(finding)

    def report(self, scene: str = "", probe_error: str | None = None) -> dict:
        return {
            "version": REPORT_VERSION,
            "scene": scene,
            "threshold": self.threshold,
            "checks": self.checks,
            "findings": [f.as_dict() for f in self.findings],
            "dropped": self.dropped,
            "max_fraction_below_threshold": round(self.max_fraction_below, 3),
            "max_below_pair": list(self.max_below_pair),
            "max_outline_share_below_threshold": round(self.max_cross_seen, 3),
            "min_size_ratio": round(self.min_ratio_seen, 3),
            "morphs": self.morphs[:50],
            "probe_error": probe_error,
        }


# ---------------------------------------------------------------------------
# manimlib adapter (duck-typed; exercised with fakes in tests)
# ---------------------------------------------------------------------------


def _class_names(mob) -> set[str]:
    return {cls.__name__ for cls in type(mob).__mro__}


def is_text_like(mob) -> bool:
    names = _class_names(mob)
    return bool(names & _TEXT_BASES) and not (names & _NOT_TEXT)


def text_of(mob) -> str:
    for attr in ("text", "tex_string", "num_string", "string"):
        value = getattr(mob, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return type(mob).__name__


def _max_opacity(mob) -> float:
    best = 0.0
    for member in mob.get_family():
        getter = getattr(member, "get_fill_opacities", None)
        if getter is None:
            continue
        try:
            if not member.has_points():
                continue
            values = getter()
        except Exception:
            continue
        if len(values):
            best = max(best, float(max(values)))
    return best


def _box(mob) -> tuple[float, float, float, float] | None:
    if not any(m.has_points() for m in mob.get_family()):
        return None
    bb = mob.get_bounding_box()
    box = (float(bb[0][0]), float(bb[0][1]), float(bb[2][0]), float(bb[2][1]))
    if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
        return None
    return box


def _frame_rects(scene) -> tuple[tuple | None, tuple | None]:
    """Visible rectangle for fixed-in-frame and for world-space mobjects.

    The world rectangle is None when the camera is rotated in 3D: world-space
    boxes then no longer match what is on screen, so those texts are skipped.
    """
    frame = getattr(scene, "frame", None)
    if frame is None:
        return None, None
    try:
        w, h = float(frame.get_width()), float(frame.get_height())
        cx, cy = (float(v) for v in frame.get_center()[:2])
        angles = frame.get_euler_angles()
        rotated = any(abs(float(a)) > 1e-3 for a in angles)
    except Exception:
        return None, None
    # Fixed-in-frame mobjects live in the default frame's coordinates.
    constants = sys.modules.get("manimlib.constants")
    fw = float(getattr(constants, "FRAME_WIDTH", w))
    fh = float(getattr(constants, "FRAME_HEIGHT", h))
    fixed = (-fw / 2, -fh / 2, fw / 2, fh / 2)
    world = None if rotated else (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    return fixed, world


def _polylines(mob) -> list:
    """Outline of one VMobject as polylines of (x, y), curves sampled."""
    polys = []
    for sub in mob.get_subpaths():
        poly = []
        for i in range(0, len(sub) - 2, 2):
            p0, p1, p2 = sub[i], sub[i + 1], sub[i + 2]
            for t in (0.0, 0.25, 0.5, 0.75):
                a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                poly.append(
                    (
                        float(a * p0[0] + b * p1[0] + c * p2[0]),
                        float(a * p0[1] + b * p1[1] + c * p2[1]),
                    )
                )
        if len(sub):
            poly.append((float(sub[-1][0]), float(sub[-1][1])))
        if len(poly) >= 2:
            polys.append(poly)
    return polys


def _fill_is_dark(mob) -> bool:
    try:
        colors = mob.get_fill_colors()
        if not colors:
            return False
        h = str(colors[0]).lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:
        return False
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < DARK_FILL


def collect_shape_items(scene) -> list[ShapeItem]:
    """Visible non-text drawn parts of the scene (strokes and fills)."""
    shapes: list[ShapeItem] = []
    seen: set[int] = set()

    def visit(mob) -> None:
        if id(mob) in seen or len(shapes) >= MAX_SHAPES:
            return
        seen.add(id(mob))
        if is_text_like(mob):
            return
        for sub in getattr(mob, "submobjects", []):
            visit(sub)
        if not mob.has_points():
            return
        try:
            fills = mob.get_fill_opacities()
            fill = float(max(fills)) if len(fills) else 0.0
            widths = mob.get_stroke_widths()
            stroke = (
                float(max(mob.get_stroke_opacities()))
                if len(widths) and float(max(widths)) > 0
                else 0.0
            )
        except Exception:
            return
        if fill <= MIN_OPACITY and stroke <= MIN_OPACITY:
            return
        polys = _polylines(mob)
        if not polys:
            return
        # A straight horizontal or vertical line has no area, so the box is
        # taken from the outline and padded a little.
        xs = [x for poly in polys for x, _ in poly]
        ys = [y for poly in polys for _, y in poly]
        box = (min(xs) - 0.02, min(ys) - 0.02, max(xs) + 0.02, max(ys) + 0.02)
        shapes.append(
            ShapeItem(type(mob).__name__, box, polys, fill, _fill_is_dark(mob))
        )

    for mob in list(getattr(scene, "mobjects", [])):
        visit(mob)
    return shapes


_references: dict = {}


def reference_height(item: TextItem) -> float | None:
    """Height the same text would have at font_size 22 (cached), or None."""
    mob = item.obj
    if mob is None:
        return None
    manimlib = sys.modules.get("manimlib")
    if manimlib is None:
        return None
    try:
        names = _class_names(mob)
        if "Tex" in names and hasattr(mob, "tex_string"):
            key = ("Tex", mob.tex_string)
            maker = lambda: manimlib.Tex(mob.tex_string, font_size=MIN_READABLE_FONT)  # noqa: E731
        elif "Text" in names and isinstance(getattr(mob, "text", None), str):
            font = getattr(mob, "font", None)
            key = ("Text", mob.text, font)
            maker = lambda: manimlib.Text(  # noqa: E731
                mob.text, font_size=MIN_READABLE_FONT, font=font
            )
        else:
            return None
        if key in _references:
            return _references[key]
        if len(_references) >= MAX_REFERENCES:
            return None
        ref = maker()
        box = _box(ref)
        _references[key] = (box[3] - box[1]) if box else None
        return _references[key]
    except Exception:
        return None


def collect_text_items(scene, min_opacity: float = MIN_OPACITY) -> list[TextItem]:
    """Visible text-like mobjects on screen, with their ancestry."""
    fixed_rect, world_rect = _frame_rects(scene)
    items: list[TextItem] = []
    seen: set[int] = set()

    def visit(mob, ancestors: tuple[int, ...]) -> None:
        if id(mob) in seen:
            return
        seen.add(id(mob))
        if is_text_like(mob):
            # A text's own glyphs (and the per-character submobjects of a
            # DecimalNumber) are never compared with each other.
            if _max_opacity(mob) <= min_opacity:
                return
            box = _box(mob)
            if box is None:
                return
            try:
                fixed = bool(mob.is_fixed_in_frame())
            except Exception:
                fixed = False
            rect = fixed_rect if fixed else world_rect
            if rect is None or intersection_area(box, rect) <= 0:
                return
            items.append(
                TextItem(
                    text=text_of(mob),
                    box=box,
                    kind=type(mob).__name__,
                    ancestors=ancestors,
                    ident=id(mob),
                    rect=rect,
                    obj=mob,
                )
            )
            return
        for sub in getattr(mob, "submobjects", []):
            visit(sub, ancestors + (id(mob),))

    for mob in list(getattr(scene, "mobjects", [])):
        visit(mob, ())
    return items


# ---------------------------------------------------------------------------
# Installation in the render child
# ---------------------------------------------------------------------------

_state: dict = {
    "recorder": None,
    "scene": "",
    "error": None,
    "path": None,
    "pid": None,  # the process that installed the probe; only it writes
    "nudges": 0,  # text moved inside the safe area (see NUDGE_ENV)
    "passive": False,  # record play times only (see PASSIVE_ENV)
    "plays": [],  # scene.time after each play()/wait(), in order
    "num_plays": 0,
}


def _record_error(where: str, exc: BaseException) -> None:
    if _state["error"] is None:
        _state["error"] = f"{where}: {type(exc).__name__}: {exc}"[:300]


def write_report() -> None:
    path = _state["path"]
    if not path:
        return
    owner = _state.get("pid")
    if owner is not None and owner != os.getpid():
        return  # a forked copy of the state must not clobber the owner's report
    try:
        recorder = _state["recorder"] or OverlapRecorder()
        data = recorder.report(_state["scene"], _state["error"])
        data["nudges"] = _state.get("nudges", 0)
        # scene.time after every play()/wait() (a skipped one included), so a
        # host that renders the scene in segments can plan the split and number
        # the animations without rendering a frame.
        data["num_plays"] = _state.get("num_plays", 0)
        data["play_times"] = list(_state.get("plays") or [])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass  # never break the render over a report


def _skipping(scene) -> bool:
    """True while a segment render skips the animations before its ``-n`` start.

    A dry run (``DRY_RUN_ENV``) skips every animation on purpose and must still
    observe all of them, so it never counts as skipping here.
    """
    if os.environ.get(DRY_RUN_ENV) == "1":
        return False
    return bool(getattr(scene, "skip_animations", False))


def _observe(scene) -> None:
    # A segment re-runs the animations before its start with skipping on; the
    # segment that renders them reports them, so this one must not.
    if _state.get("passive") or _skipping(scene):
        return
    try:
        if _state["recorder"] is None:
            _state["recorder"] = OverlapRecorder()
        _state["scene"] = type(scene).__name__
        items = collect_text_items(scene)
        now = float(getattr(scene, "time", 0.0))
        _state["recorder"].observe(now, items)
        margins = parse_margins(os.environ.get(SAFE_MARGINS_ENV))
        _state["recorder"].observe_bounds(now, items, margins)
        # Shorts only (safe margins set): font_size 22 is the phone floor, and
        # text-on-shape is calibrated on the layout kit. In landscape it was
        # never measured as a retry trigger, so it stays off there for now.
        if margins is not None:
            _state["recorder"].observe_small(now, items, reference_height)
            if items:
                _state["recorder"].observe_shapes(
                    now, items, collect_shape_items(scene)
                )
    except Exception as exc:  # a probe bug must never break a render
        _record_error("observe", exc)


def nudge_offset(
    box: tuple[float, float, float, float],
    rect: tuple[float, float, float, float],
    limit: float = NUDGE_LIMIT,
) -> tuple[float, float] | None:
    """(dx, dy) that moves ``box`` inside ``rect``.

    None when no move is needed, the box does not fit, or the move on either
    axis exceeds ``limit``.
    """
    if box[2] - box[0] > rect[2] - rect[0] or box[3] - box[1] > rect[3] - rect[1]:
        return None
    dx = max(0.0, rect[0] - box[0]) - max(0.0, box[2] - rect[2])
    dy = max(0.0, rect[1] - box[1]) - max(0.0, box[3] - rect[3])
    if dx == 0.0 and dy == 0.0:
        return None
    if abs(dx) > limit or abs(dy) > limit:
        return None
    return dx, dy


def _text_only(mob) -> bool:
    """True when every drawn part of ``mob`` belongs to a text-like mobject."""
    covered: set[int] = set()
    for m in mob.get_family():
        if is_text_like(m):
            covered.update(id(f) for f in m.get_family())
    if not covered:
        return False
    return all(id(m) in covered for m in mob.get_family() if m.has_points())


def _nudge_new_text(scene, mobjects) -> None:
    """Move new, slightly-out text inside the shorts safe area (see NUDGE_ENV)."""
    if _state.get("passive") or os.environ.get(NUDGE_ENV, "1") == "0":
        return
    margins = parse_margins(os.environ.get(SAFE_MARGINS_ENV))
    if margins is None:
        return
    try:
        fixed, world = _frame_rects(scene)
        present = {id(m) for top in scene.mobjects for m in top.get_family()}
        for mob in mobjects:
            if mob is None or id(mob) in present or not _text_only(mob):
                continue
            rect = (
                fixed if getattr(mob, "is_fixed_in_frame", lambda: False)() else world
            )
            box = _box(mob)
            if rect is None or box is None:
                continue
            move = nudge_offset(box, inset_rect(rect, margins))
            if move is not None:
                mob.shift([move[0], move[1], 0.0])
                if not _skipping(scene):  # a skipped one is counted where rendered
                    _state["nudges"] = _state.get("nudges", 0) + 1
    except Exception as exc:  # a probe bug must never break a render
        _record_error("nudge", exc)


def _glyph_items(anim) -> list[tuple[int, tuple, float]]:
    """(sub-animation index, box, area) for each visible glyph of a morph."""
    out = []
    for index, sub in enumerate(getattr(anim, "animations", [])):
        for glyph in sub.mobject.family_members_with_points():
            if _max_opacity(glyph) <= MIN_OPACITY:
                continue
            box = _box(glyph)
            if box is not None:
                out.append((index, box, _area(box)))
    return out


def _observe_morphs(scene, animations) -> None:
    if _state.get("passive") or _skipping(scene):
        return
    try:
        for anim in animations:
            if "TransformMatchingParts" not in _class_names(anim):
                continue
            source = text_of(getattr(anim, "source", anim))
            target = text_of(getattr(anim, "target", anim))
            anim.interpolate(0.5)
            try:
                glyphs = _glyph_items(anim)
            finally:
                anim.interpolate(0.0)
            if _state["recorder"] is None:
                _state["recorder"] = OverlapRecorder()
            mid = float(getattr(scene, "time", 0.0)) + float(anim.run_time) / 2
            _state["recorder"].observe_morph(mid, source, target, glyphs)
    except Exception as exc:
        _record_error("morph", exc)


def _frames_in(run_time: float, fps: float) -> int:
    """Frames manimgl draws for ``run_time`` (what ``get_time_progression`` makes)."""
    import numpy as np

    return len(np.arange(0, run_time, 1 / fps))


def _patch_skipping(scene_cls) -> None:
    """Make a skipped animation advance the scene exactly like a rendered one.

    Two differences in manimgl 1.7.2 matter when a scene is rendered from
    animation ``-n start`` (the animations before it are run with skipping on):

    * A skipped animation advances ``scene.time`` and the updaters by exactly
      ``run_time`` in one step, while a rendered one advances by whole frames
      (``ceil(run_time * fps) / fps``). The skipped time is rounded up to the
      same whole frames, so ``scene.time`` at the start of a segment equals the
      time of the full render and the frames line up.
    * ``finish_animations`` calls ``update_mobjects(run_time)`` a second time
      when skipping, so every updater sees twice the elapsed time for each
      skipped ``play()`` (a dot moved by ``dt`` ended up twice as far). A
      rendered animation only calls ``update_mobjects(0)``; skipping now does
      the same.

    Both changes apply only while skipping, so a full render is untouched.
    """
    original_progression = scene_cls.get_time_progression
    original_finish = scene_cls.finish_animations

    def get_time_progression(
        self, run_time, n_iterations=None, desc="", override_skip_animations=False
    ):
        if getattr(self, "skip_animations", False) and not override_skip_animations:
            try:
                fps = float(self.camera.fps)
                return [_frames_in(run_time, fps) / fps]
            except Exception as exc:
                _record_error("skip_time", exc)
        return original_progression(
            self, run_time, n_iterations, desc, override_skip_animations
        )

    def finish_animations(self, animations):
        if not getattr(self, "skip_animations", False):
            return original_finish(self, animations)
        for animation in animations:
            animation.finish()
            animation.clean_up_from_scene(self)
        self.update_mobjects(0)

    scene_cls.get_time_progression = get_time_progression
    scene_cls.finish_animations = finish_animations


def patch_scene_module(module) -> None:
    """Wrap Scene.post_play and Scene.tear_down in ``manimlib.scene.scene``."""
    scene_cls = getattr(module, "Scene", None)
    if scene_cls is None or getattr(scene_cls, "_manimgen_overlap_probe", False):
        return
    original_post_play = scene_cls.post_play
    original_tear_down = scene_cls.tear_down
    original_begin = scene_cls.begin_animations
    original_add = getattr(scene_cls, "add", None)

    def add(self, *mobjects, **kwargs):
        _nudge_new_text(self, mobjects)
        return original_add(self, *mobjects, **kwargs)

    def post_play(self, *args, **kwargs):
        result = original_post_play(self, *args, **kwargs)
        try:
            _state["num_plays"] = int(getattr(self, "num_plays", 0))
            plays = _state.setdefault("plays", [])
            if len(plays) < MAX_PLAY_TIMES:
                plays.append(round(float(getattr(self, "time", 0.0)), 4))
        except Exception as exc:
            _record_error("play_times", exc)
        _observe(self)
        return result

    def begin_animations(self, animations, *args, **kwargs):
        _nudge_new_text(self, [getattr(a, "mobject", None) for a in animations])
        result = original_begin(self, animations, *args, **kwargs)
        _observe_morphs(self, animations)
        return result

    def tear_down(self, *args, **kwargs):
        try:
            return original_tear_down(self, *args, **kwargs)
        finally:
            write_report()

    try:
        if hasattr(scene_cls, "get_time_progression") and hasattr(
            scene_cls, "finish_animations"
        ):
            _patch_skipping(scene_cls)
    except Exception as exc:
        _record_error("patch_skipping", exc)
    scene_cls.post_play = post_play
    scene_cls.tear_down = tear_down
    scene_cls.begin_animations = begin_animations
    if original_add is not None:
        scene_cls.add = add
    scene_cls._manimgen_overlap_probe = True


_TARGET = "manimlib.scene.scene"


class _PatchingLoader:
    """Delegates to the real loader, then patches the freshly executed module."""

    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self._loader.exec_module(module)
        try:
            patch_scene_module(module)
        except Exception as exc:
            _record_error("patch", exc)


class _SceneImportHook:
    """``sys.meta_path`` finder that patches manimlib's Scene on first import.

    Importing manimlib from sitecustomize would run manimlib.config (which
    parses the command line) before manimgl's own start-up, so the patch waits
    until manimgl imports the module itself.
    """

    def find_spec(self, name, path=None, target=None):
        if name != _TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader)
                return spec
        return None


def install() -> None:
    """Activate the probe when ``MANIMGEN_OVERLAP_REPORT`` names a file."""
    path = os.environ.get(REPORT_ENV)
    if not path:
        return
    _state["path"] = path
    _state["pid"] = os.getpid()
    _state["passive"] = os.environ.get(PASSIVE_ENV, "") == "1"
    os.environ.pop(PASSIVE_ENV, None)
    # Helper processes (multiprocessing's resource tracker, which spawn-based
    # platforms start) inherit the environment and would load this probe too;
    # they must not own or overwrite the report.
    os.environ.pop(REPORT_ENV, None)
    try:
        module = sys.modules.get(_TARGET)
        if module is not None:
            patch_scene_module(module)
        else:
            sys.meta_path.insert(0, _SceneImportHook())
    except Exception as exc:
        _record_error("install", exc)
    # Write an empty report now so a crash before tear-down still leaves a file
    # saying the probe was present; atexit rewrites it with the final state.
    write_report()
    atexit.register(write_report)
