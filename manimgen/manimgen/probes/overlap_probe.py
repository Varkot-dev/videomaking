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
MAX_PER_TEXT = 3  # findings that may name the same string
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


@dataclass
class Finding:
    time: float
    a: str
    b: str
    fraction: float
    a_kind: str = "Text"
    b_kind: str = "Text"

    def as_dict(self) -> dict:
        return {
            "time": round(self.time, 2),
            "a": self.a,
            "b": self.b,
            "fraction": round(self.fraction, 2),
            "a_kind": self.a_kind,
            "b_kind": self.b_kind,
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
        key = tuple(sorted((finding.a, finding.b)))
        if key in self.seen:
            return
        self.seen.add(key)
        # One caption laid over a whole row of labels would otherwise fill the
        # list with the same defect and hide later ones.
        per_text = sum(1 for f in self.findings if {f.a, f.b} & {finding.a, finding.b})
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

_state: dict = {"recorder": None, "scene": "", "error": None, "path": None}


def _record_error(where: str, exc: BaseException) -> None:
    if _state["error"] is None:
        _state["error"] = f"{where}: {type(exc).__name__}: {exc}"[:300]


def write_report() -> None:
    path = _state["path"]
    if not path:
        return
    try:
        recorder = _state["recorder"] or OverlapRecorder()
        data = recorder.report(_state["scene"], _state["error"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass  # never break the render over a report


def _observe(scene) -> None:
    try:
        if _state["recorder"] is None:
            _state["recorder"] = OverlapRecorder()
        _state["scene"] = type(scene).__name__
        items = collect_text_items(scene)
        _state["recorder"].observe(float(getattr(scene, "time", 0.0)), items)
    except Exception as exc:  # a probe bug must never break a render
        _record_error("observe", exc)


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


def patch_scene_module(module) -> None:
    """Wrap Scene.post_play and Scene.tear_down in ``manimlib.scene.scene``."""
    scene_cls = getattr(module, "Scene", None)
    if scene_cls is None or getattr(scene_cls, "_manimgen_overlap_probe", False):
        return
    original_post_play = scene_cls.post_play
    original_tear_down = scene_cls.tear_down
    original_begin = scene_cls.begin_animations

    def post_play(self, *args, **kwargs):
        result = original_post_play(self, *args, **kwargs)
        _observe(self)
        return result

    def begin_animations(self, animations, *args, **kwargs):
        result = original_begin(self, animations, *args, **kwargs)
        _observe_morphs(self, animations)
        return result

    def tear_down(self, *args, **kwargs):
        try:
            return original_tear_down(self, *args, **kwargs)
        finally:
            write_report()

    scene_cls.post_play = post_play
    scene_cls.tear_down = tear_down
    scene_cls.begin_animations = begin_animations
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
