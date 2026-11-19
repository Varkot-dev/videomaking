# ruff: noqa: F405
"""Layout kit for vertical YouTube Shorts: ``ShortScene``.

A generated Short inherits from :class:`ShortScene` instead of ``Scene``. The
base class owns every placement decision, so the scene only says WHAT to show:

    hook / key      fixed bands at the top (title and the one key line)
    diagram         the main figure, scaled to fit its band and centred
    label           text on or beside a shape, put where nothing is covered
    swap_hook/key   replace text without the old and new ghosting together
    finish          fade out anything left half-faded (runs by itself at the end)

Frame facts (verified by real 1080x1920 renders): the frame is 4.5 units wide
and 8 tall. Text is only readable inside x -2.0..1.4, y -2.0..2.8 (the app draws
over the right edge and the bottom). Burned-in captions sit at roughly y -1.5 to
-2.0, so the figure and its labels live in y -1.4..1.4 (labels may also use the
free space above the figure) and never lower.

All motion is still ordinary ManimGL (``self.play(FadeIn(...))``). The kit only
decides geometry and ordering.

Constraints on this file: it runs inside the manimgl child with nothing but
manimlib and the standard library, and it is held to the same static safety
gate as a generated scene (one class, no private attributes, no decorators other
than ``property``), so the geometry is plain module-level functions over tuples
of floats. A box is ``(x0, y0, x1, y1)``. The unit tests exercise those
functions without any GL context.
"""

import math
import textwrap

from manimlib import *  # noqa: F403

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CX = -0.3  # centre line of the safe area
SAFE = (-2.0, -2.0, 1.4, 2.8)  # where anything readable may be
LABEL_AREA = (-2.0, -1.4, 1.4, 2.8)  # labels stay above the caption band
DIAGRAM_BAND = (-2.0, -1.4, 1.4, 1.4)
HOOK_TOP = 2.75
KEY_TOP = 1.9
MAX_WIDTH = 3.3
HOOK_MAX_HEIGHT = 0.85  # hook top (2.75) down to just above the key line
MIN_FONT = 22  # smallest font_size that reads on a phone
HOOK_FONT = 28
KEY_FONT = 36
LABEL_FONT = 28
FONT_STEPS = (36, 28, 22)  # the sizes codeguard allows in a short, large to small
FADE_OUT_TIME = 0.25  # fully fade the old text before the new one appears
LABEL_BUFF = 0.12

# A candidate label spot is "good" when every violation is at or below these.
TOL_OUTSIDE = 0.01  # share of the label outside LABEL_AREA
TOL_TEXT = 0.02  # share of the label over another text
TOL_CROSS = 0.05  # outline length inside the label, relative to its longest side
TOL_FILL = 0.12  # share of the label over a filled shape that is not its own
INSIDE_FILL_MAX = 0.3  # a label goes inside a shape only if its fill is this faint
SOLID_FILL = 0.02  # fill opacity above which a shape counts as filled
CLEARANCE = 0.03  # labels keep this far from outlines and other text

TEXT_BASES = (StringMobject, DecimalNumber)
NOT_TEXT = (Brace, Checkmark, Exmark)


# ---------------------------------------------------------------------------
# Pure geometry (no manimlib objects)
# ---------------------------------------------------------------------------


def box_w(box):
    return box[2] - box[0]


def box_h(box):
    return box[3] - box[1]


def box_area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def box_center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def box_around(cx, cy, w, h):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def box_inflate(box, margin):
    return (box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin)


def box_union(boxes):
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def box_overlap_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h


def share_outside(box, rect):
    """Share of ``box`` that lies outside ``rect`` (0.0 inside, 1.0 fully out)."""
    area = box_area(box)
    if area <= 0:
        return 0.0
    return max(0.0, 1.0 - box_overlap_area(box, rect) / area)


def clip_segment_length(p, q, box):
    """Length of the part of segment p-q that lies inside ``box`` (Liang-Barsky)."""
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
    return (t1 - t0) * math.hypot(dx, dy)


def polyline_length_in_box(poly, box):
    """Length of a polyline (list of (x, y)) inside ``box``."""
    total = 0.0
    for a, b in zip(poly, poly[1:]):
        total += clip_segment_length(a, b, box)
    return total


def outline_in_box(polys, box):
    return sum(polyline_length_in_box(poly, box) for poly in polys)


def point_in_polys(x, y, polys):
    """Even-odd point-in-polygon over several closed polylines (holes work)."""
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


def fill_share(box, polys, n=6):
    """Share of ``box`` covered by the filled polygons (n x n sample grid)."""
    if not polys:
        return 0.0
    hits = 0
    for i in range(n):
        for j in range(n):
            x = box[0] + (i + 0.5) / n * (box[2] - box[0])
            y = box[1] + (j + 0.5) / n * (box[3] - box[1])
            if point_in_polys(x, y, polys):
                hits += 1
    return hits / (n * n)


def point_segment_distance(px, py, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def deepest_point(polys, box, n=14):
    """The grid point inside the polygons that is farthest from every outline."""
    best = None
    best_d = 0.0
    for i in range(n):
        for j in range(n):
            x = box[0] + (i + 0.5) / n * (box[2] - box[0])
            y = box[1] + (j + 0.5) / n * (box[3] - box[1])
            if not point_in_polys(x, y, polys):
                continue
            d = min(
                point_segment_distance(x, y, a, b)
                for poly in polys
                for a, b in zip(poly, poly[1:])
            )
            if d > best_d:
                best_d = d
                best = (x, y)
    return best


def polygon_centroid(poly):
    """Mean of the polyline's points (vertex mean; fine for convex shapes)."""
    n = len(poly)
    return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)


def is_straight(poly, tol=0.015):
    """True when every point of the polyline lies within ``tol`` of the chord."""
    a, b = poly[0], poly[-1]
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.05:
        return False
    return all(point_segment_distance(p[0], p[1], a, b) <= tol for p in poly)


def snap_font(size):
    """Largest allowed font size that does not exceed ``size`` (min 22)."""
    for step in FONT_STEPS:
        if size >= step:
            return step
    return MIN_FONT


def normalize_prefer(prefer):
    """Canonical placement name, or None when ``prefer`` is empty or unknown."""
    if not isinstance(prefer, str):
        return None
    name = prefer.strip().lower()
    table = {
        "inside": "inside",
        "in": "inside",
        "inner": "inside",
        "outside": "outside",
        "out": "outside",
        "outward": "outside",
        "right": "right",
        "left": "left",
        "above": "above",
        "up": "above",
        "over": "above",
        "top": "above",
        "below": "below",
        "down": "below",
        "under": "below",
        "bottom": "below",
    }
    return table.get(name)


def outward_name(target_box, diagram_box):
    """Which side of the target faces away from the diagram's centre, or None."""
    tcx, tcy = box_center(target_box)
    dcx, dcy = box_center(diagram_box)
    vx = (tcx - dcx) / max(box_w(diagram_box) / 2, 0.1)
    vy = (tcy - dcy) / max(box_h(diagram_box) / 2, 0.1)
    if abs(vx) < 0.15 and abs(vy) < 0.15:
        return None
    if abs(vx) >= abs(vy):
        return "right" if vx > 0 else "left"
    return "above" if vy > 0 else "below"


def side_order(kind, prefer, outward):
    """Candidate names in the order they are tried."""
    base = ["right", "left", "above", "below"]
    order = []
    if prefer in ("right", "left", "above", "below"):
        order.append(prefer)
    if kind == "closed" and prefer != "outside":
        order.append("inside")
    if prefer == "outside" or kind != "closed":
        if outward:
            order.append(outward)
    if prefer == "inside" and "inside" not in order:
        order.insert(0, "inside")
    if outward and outward not in order:
        order.append(outward)
    for name in base:
        if name not in order:
            order.append(name)
    if kind == "closed" and "inside" not in order:
        order.insert(0, "inside")
    return order


def side_positions(box, w, h, buff, name, wide):
    """Label centres on one side of ``box``: centred, then slid along the side."""
    cx, cy = box_center(box)
    if name in ("right", "left"):
        x = box[2] + buff + w / 2 if name == "right" else box[0] - buff - w / 2
        span = box_h(box)
        offsets = [0.0] if span < 0.25 or not wide else [0.0, 0.3 * span, -0.3 * span]
        return [(name, x, cy + off) for off in offsets]
    y = box[3] + buff + h / 2 if name == "above" else box[1] - buff - h / 2
    span = box_w(box)
    offsets = [0.0] if span < 0.25 or not wide else [0.0, 0.3 * span, -0.3 * span]
    return [(name, cx + off, y) for off in offsets]


def corner_positions(box, w, h, buff):
    """Diagonal spots around a point-like target."""
    k = buff * 0.7
    return [
        ("above-right", box[2] + k + w / 2, box[3] + k + h / 2),
        ("above-left", box[0] - k - w / 2, box[3] + k + h / 2),
        ("below-right", box[2] + k + w / 2, box[1] - k - h / 2),
        ("below-left", box[0] - k - w / 2, box[1] - k - h / 2),
    ]


def line_positions(ends, w, h, buff, prefer, diagram_center):
    """Spots on either side of a straight segment, outward side first."""
    (ax, ay), (bx, by) = ends
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length
    normals = [(-uy, ux), (uy, -ux)]
    mx, my = (ax + bx) / 2, (ay + by) / 2

    def score(nrm):
        if prefer in ("right", "left", "above", "below"):
            want = {
                "right": (1, 0),
                "left": (-1, 0),
                "above": (0, 1),
                "below": (0, -1),
            }[prefer]
            return nrm[0] * want[0] + nrm[1] * want[1]
        out = (mx - diagram_center[0]) * nrm[0] + (my - diagram_center[1]) * nrm[1]
        return -out if prefer == "inside" else out

    normals.sort(key=score, reverse=True)
    names = ("out", "in")
    found = []
    for rank, nrm in enumerate(normals):
        reach = buff + 0.5 * (abs(nrm[0]) * w + abs(nrm[1]) * h)
        for t in (0.5, 0.3, 0.7):
            px, py = ax + t * (bx - ax), ay + t * (by - ay)
            found.append((names[rank], px + nrm[0] * reach, py + nrm[1] * reach))
    return found


def candidate_violations(cand, w, h, shapes, text_boxes, own, inside, area=LABEL_AREA):
    """How badly a label centred at ``cand`` breaks each placement rule.

    ``shapes`` are dicts with ``polys``, ``fill`` and ``ident``; ``own`` holds
    the idents of the target's own shapes; ``inside`` is True when the label is
    meant to sit inside its target (its own fill is then not an obstacle).
    """
    box = box_around(cand[0], cand[1], w, h)
    near = box_inflate(box, CLEARANCE)
    area_of_box = box_area(box)
    out = share_outside(box, area)
    over_text = 0.0
    for tb in text_boxes:
        over_text += (
            box_overlap_area(near, box_inflate(tb, CLEARANCE / 2)) / area_of_box
        )
    crossing = 0.0
    over_fill = 0.0
    for shape in shapes:
        sb = shape["box"]
        if box_overlap_area(near, sb) <= 0:
            continue
        crossing += outline_in_box(shape["polys"], near)
        if shape["fill"] > SOLID_FILL and not (inside and shape["ident"] in own):
            over_fill += fill_share(box, shape["polys"])
    crossing /= max(w, h)
    return {"outside": out, "text": over_text, "cross": crossing, "fill": over_fill}


def is_good(v):
    return (
        v["outside"] <= TOL_OUTSIDE
        and v["text"] <= TOL_TEXT
        and v["cross"] <= TOL_CROSS
        and v["fill"] <= TOL_FILL
    )


def badness(v):
    return v["outside"] * 20 + v["text"] * 10 + v["cross"] * 8 + v["fill"] * 6


def violation_note(v):
    bad = []
    if v["outside"] > TOL_OUTSIDE:
        bad.append("leaves the label area")
    if v["text"] > TOL_TEXT:
        bad.append("covers other text")
    if v["cross"] > TOL_CROSS:
        bad.append("crosses a line")
    if v["fill"] > TOL_FILL:
        bad.append("sits on a filled shape")
    return ", ".join(bad)


def fit_scale(w, h, band, pad):
    """Scale that fits a w x h figure into ``band`` minus ``pad`` (l, b, r, t)."""
    avail_w = box_w(band) - pad[0] - pad[2]
    avail_h = box_h(band) - pad[1] - pad[3]
    return min(avail_w / w, avail_h / h)


def wrap_width_chars(text, width, limit):
    """Characters per line so a line of ``text`` rendered ``width`` wide fits ``limit``."""
    per_char = width / max(len(text), 1)
    return max(4, int(limit / per_char * 0.97))


def split_lines(text, width, limit):
    """Break ``text`` (rendered ``width`` wide) into lines that fit ``limit``."""
    if width <= limit or " " not in text.strip():
        return [text]
    return textwrap.wrap(text, wrap_width_chars(text, width, limit)) or [text]


# ---------------------------------------------------------------------------
# manimlib adapters
# ---------------------------------------------------------------------------


def is_text_like(mob):
    return isinstance(mob, TEXT_BASES) and not isinstance(mob, NOT_TEXT)


def is_brace(mob):
    return isinstance(mob, Brace)


def box_of(mob):
    """(x0, y0, x1, y1) of a mobject, or None when it draws nothing."""
    if not any(m.has_points() for m in mob.get_family()):
        return None
    bb = mob.get_bounding_box()
    return (float(bb[0][0]), float(bb[0][1]), float(bb[2][0]), float(bb[2][1]))


def array_max(values):
    return float(max(values)) if len(values) else 0.0


def fill_of(mob):
    return array_max(mob.get_fill_opacities()) if mob.has_points() else 0.0


def stroke_of(mob):
    if not mob.has_points():
        return 0.0
    widths = mob.get_stroke_widths()
    if not len(widths) or float(max(widths)) <= 0:
        return 0.0
    return array_max(mob.get_stroke_opacities())


def family_opacity(mob):
    """Largest fill or stroke opacity anywhere in the family."""
    best = 0.0
    for m in mob.get_family():
        if m.has_points():
            best = max(best, fill_of(m), stroke_of(m))
    return best


def mob_polylines(mob):
    """One polyline per subpath of a single VMobject (curves sampled)."""
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


def shape_infos(mobs):
    """Visible, non-text drawn parts of ``mobs`` as dicts (box, polys, fill)."""
    infos = []
    for top in mobs:
        stack = [top]
        while stack:
            m = stack.pop()
            if is_text_like(m):
                continue
            stack.extend(m.submobjects)
            if not m.has_points():
                continue
            fill, stroke = fill_of(m), stroke_of(m)
            if fill <= SOLID_FILL and stroke <= 0.02:
                continue
            polys = mob_polylines(m)
            if not polys:
                continue
            infos.append(
                {
                    "ident": id(m),
                    "box": box_of(m),
                    "polys": polys,
                    "fill": fill,
                    "stroke": stroke,
                    "mob": m,
                }
            )
    return infos


def target_info(on):
    """Describe a label target: a mobject, a point, or a (p, q) segment."""
    if (
        isinstance(on, (tuple, list))
        and len(on) == 2
        and not hasattr(on[0], "get_family")
    ):
        p, q = (float(on[0][0]), float(on[0][1])), (float(on[1][0]), float(on[1][1]))
        box = (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
        if math.hypot(q[0] - p[0], q[1] - p[1]) < 0.05:
            return None
        return {
            "kind": "line",
            "box": box,
            "ends": (p, q),
            "polys": [],
            "idents": set(),
            "fill": 0.0,
        }
    if hasattr(on, "get_family"):
        parts = [on] if is_text_like(on) else on.get_family()
        box = box_of(on)
        if box is None:
            return None
        if is_text_like(on):
            return {
                "kind": "box",
                "box": box,
                "polys": [],
                "idents": set(),
                "fill": 0.0,
            }
        infos = shape_infos([on])
        polys = [poly for s in infos for poly in s["polys"]]
        fill = max([s["fill"] for s in infos] or [0.0])
        info = {
            "kind": "open",
            "box": box,
            "polys": polys,
            "idents": {s["ident"] for s in infos},
            "fill": fill,
        }
        if parts and is_brace(on):
            return info
        if box_w(box) < 0.3 and box_h(box) < 0.3:
            info["kind"] = "point"
        elif len(polys) == 1 and is_straight(polys[0]):
            info["kind"] = "line"
            info["ends"] = (polys[0][0], polys[0][-1])
        elif polys and all(
            math.hypot(p[0][0] - p[-1][0], p[0][1] - p[-1][1]) < 1e-3 for p in polys
        ):
            info["kind"] = "closed"
        return info
    try:
        x, y = float(on[0]), float(on[1])
    except (TypeError, ValueError, IndexError):
        return None
    return {
        "kind": "point",
        "box": (x - 0.02, y - 0.02, x + 0.02, y + 0.02),
        "polys": [],
        "idents": set(),
        "fill": 0.0,
    }


def inside_positions(info, w, h):
    """Spots inside a closed target: box centre, vertex mean, deepest point."""
    polys = info["polys"]
    spots = []
    cx, cy = box_center(info["box"])
    if point_in_polys(cx, cy, polys):
        spots.append(("inside", cx, cy))
    biggest = max(polys, key=len)
    mx, my = polygon_centroid(biggest)
    if point_in_polys(mx, my, polys):
        spots.append(("inside", mx, my))
    deep = deepest_point(polys, info["box"])
    if deep is not None:
        spots.append(("inside", deep[0], deep[1]))
    return spots


def label_candidates(info, w, h, buff, prefer, diagram_box):
    """Every spot to try for a label, in priority order: (name, cx, cy)."""
    kind = info["kind"]
    box = info["box"]
    dcenter = box_center(diagram_box)
    if kind == "line":
        return line_positions(info["ends"], w, h, buff, prefer, dcenter)
    outward = outward_name(box, diagram_box)
    if kind == "point":
        names = side_order("point", prefer, outward)
        found = []
        for name in names:
            found.extend(side_positions(box, w, h, buff, name, False))
        return found + corner_positions(box, w, h, buff)
    found = []
    for name in side_order(kind, prefer, outward):
        if name == "inside":
            if kind == "closed" and info["fill"] <= INSIDE_FILL_MAX:
                found.extend(inside_positions(info, w, h))
        else:
            found.extend(side_positions(box, w, h, buff, name, True))
    return found


def make_text(content, size, color, tex=False):
    if tex:
        mob = Tex(content, font_size=size)
    else:
        mob = Text(content, font_size=size)
    mob.set_color(color)
    return mob


# ---------------------------------------------------------------------------
# The scene
# ---------------------------------------------------------------------------


class ShortScene(Scene):
    """Base class for a vertical Short. See the module docstring."""

    def ks(self):
        """The kit's bookkeeping dict, created on first use."""
        state = getattr(self, "kit_state", None)
        if state is None:
            state = {
                "hook": None,
                "key": None,
                "texts": [],  # mobjects that labels must not cover
                "shapes": [],  # (mobject, opacity it had when registered)
                "diagram": None,
                "scale": 1.0,
                "findings": [],
                "finished": False,
            }
            self.kit_state = state
        return state

    @property
    def findings(self):
        """Placement problems the kit could not avoid (also printed)."""
        return self.ks()["findings"]

    @property
    def diagram_scale(self):
        """Scale applied to the diagram by :meth:`diagram` (1.0 before it)."""
        return self.ks()["scale"]

    def note(self, message):
        self.ks()["findings"].append(message)
        print("[scenekit] " + message)

    # -- text bands --------------------------------------------------------

    def build_lines(self, lines, sizes, color, tex, wrap, max_height):
        """Stack ``lines`` (strings or mobjects) at the largest size that fits."""
        group = None
        used = sizes[-1]
        for size in sizes:
            mobs = []
            for line in lines:
                if not isinstance(line, str):
                    mobs.append(line)
                    continue
                first = make_text(line, size, color, tex)
                parts = (
                    split_lines(line, first.get_width(), MAX_WIDTH)
                    if wrap and not tex
                    else [line]
                )
                if len(parts) == 1:
                    mobs.append(first)
                else:
                    mobs.extend(make_text(part, size, color, tex) for part in parts)
            group = VGroup(*mobs).arrange(DOWN, buff=0.12)
            used = size
            if group.get_width() <= MAX_WIDTH and (
                max_height is None or group.get_height() <= max_height
            ):
                break
        if group.get_width() > MAX_WIDTH:
            group.set_width(MAX_WIDTH)
            self.note(
                "text %r is wider than %.1f units even at font_size %d; "
                "it was shrunk to fit. Shorten it."
                % (str(lines[0])[:30], MAX_WIDTH, used)
            )
        return group, used

    def register_text(self, mob):
        self.ks()["texts"].append(mob)

    def hook(self, *lines, add=True):
        """The question at the top. Added at once so frame 0 shows it.

        Each string is word-wrapped to fit 3.3 units; the top edge sits at y 2.75.
        Returns the Text (one line) or a VGroup of lines.
        """
        group, _size = self.build_lines(
            lines, (HOOK_FONT, MIN_FONT), WHITE, False, True, HOOK_MAX_HEIGHT
        )
        group.move_to([CX, HOOK_TOP, 0], aligned_edge=UP)
        mob = group[0] if len(group) == 1 else group
        self.ks()["hook"] = mob
        self.register_text(mob)
        if add:
            self.add(mob)
        return mob

    def key(self, *lines, color=GOLD, tex=False):
        """The key words or equation, top edge at y 1.9. Not yet on screen.

        One line is the norm; several lines are stacked (an equation derivation).
        Strings become Text, or Tex when ``tex=True``; Tex/Text mobjects pass
        through. Never wrapped: a too-wide line drops to a smaller font.
        """
        group, _size = self.build_lines(lines, FONT_STEPS, color, tex, False, None)
        group.move_to([CX, KEY_TOP, 0], aligned_edge=UP)
        mob = group[0] if len(group) == 1 else group
        self.ks()["key"] = mob
        self.register_text(mob)
        return mob

    def unregister(self, mobs):
        """Forget texts and shapes that are going away."""
        ks = self.ks()
        gone = set()
        for mob in mobs:
            gone.update(id(m) for m in mob.get_family())
        ks["texts"] = [t for t in ks["texts"] if id(t) not in gone]
        ks["shapes"] = [s for s in ks["shapes"] if id(s[0]) not in gone]
        for slot in ("hook", "key"):
            if ks[slot] is not None and id(ks[slot]) in gone:
                ks[slot] = None

    def drop(self, *mobs, run_time=FADE_OUT_TIME):
        """Fade ``mobs`` out in a play of their own and forget them.

        Costs ``run_time`` seconds (default 0.25).
        """
        self.play(*[FadeOut(m) for m in mobs], run_time=run_time)
        self.unregister(mobs)
        return run_time

    def swap_text(self, slot, build, run_time):
        old = self.ks()[slot]
        if old is not None:
            self.play(FadeOut(old), run_time=FADE_OUT_TIME)
            self.unregister([old])
        else:
            self.wait(FADE_OUT_TIME)
        new = build()
        self.play(FadeIn(new), run_time=run_time)
        return new

    def swap_hook(self, *lines, run_time=0.5):
        """Replace the hook: old text fully out (0.25 s), then the new text in.

        Costs 0.25 + ``run_time`` seconds (always, even with no old hook), which the
        scene must count in its cue; the timing check counts it too. Returns the
        new hook.
        """
        return self.swap_text("hook", lambda: self.hook(*lines, add=False), run_time)

    def swap_key(self, *lines, color=GOLD, tex=False, run_time=0.5):
        """Replace the key line: old out (0.25 s), then new in. Costs 0.25 + run_time.

        Returns the new key.
        """
        return self.swap_text(
            "key", lambda: self.key(*lines, color=color, tex=tex), run_time
        )

    # -- diagram -----------------------------------------------------------

    def register(self, *mobs):
        """Tell the kit about shapes that labels must keep clear of."""
        ks = self.ks()
        known = {id(s[0]) for s in ks["shapes"]}
        for info in shape_infos(list(mobs)):
            if info["ident"] not in known:
                ks["shapes"].append((info["mob"], max(info["fill"], info["stroke"])))

    def diagram(self, *mobs, pad=0.4):
        """Group ``mobs``, scale them into the diagram band and centre them.

        The band is x -2.0..1.4, y -1.4..1.4. ``pad`` (a number, or a
        (left, bottom, right, top) tuple) keeps that much room free inside the
        band for labels outside the shapes; pass 0 when every label goes inside
        a shape. Shapes only: Text and Tex belong to :meth:`label`, because text
        scaled with the figure would shrink below the readable minimum.
        Returns the VGroup. Nothing is added to the scene.
        """
        group = VGroup(*mobs)
        for m in group.get_family():
            if is_text_like(m):
                raise ValueError(
                    "diagram() takes shapes only; build text with self.label(...) "
                    "so it keeps its readable size"
                )
        if isinstance(pad, (int, float)):
            pad = (pad, pad, pad, pad)
        box = box_of(group)
        if box is None or box_w(box) <= 0 or box_h(box) <= 0:
            raise ValueError("diagram() needs shapes with some width and height")
        scale = fit_scale(box_w(box), box_h(box), DIAGRAM_BAND, pad)
        group.scale(scale)
        cx = CX + (pad[0] - pad[2]) / 2
        cy = (DIAGRAM_BAND[1] + DIAGRAM_BAND[3]) / 2 + (pad[1] - pad[3]) / 2
        group.move_to([cx, cy, 0])
        ks = self.ks()
        ks["diagram"] = group
        ks["scale"] = scale
        self.register(group)
        return group

    def side(self, shape, index):
        """The index-th side of a polygon as a (start, end) pair for label(on=...).

        Reads the polygon's CURRENT vertices, so call it after diagram().
        """
        verts = shape.get_start_anchors()
        n = len(verts)
        return (verts[index % n].copy(), verts[(index + 1) % n].copy())

    # -- labels ------------------------------------------------------------

    def text_boxes(self):
        boxes = []
        for t in self.ks()["texts"]:
            b = box_of(t)
            if b is not None:
                boxes.append(b)
        return boxes

    def diagram_box(self):
        group = self.ks()["diagram"]
        box = box_of(group) if group is not None else None
        return box if box is not None else DIAGRAM_BAND

    def label(self, text, on, prefer=None, color=WHITE, size=None, tex=False):
        """A label for ``on`` (a mobject, a point, or ``self.side(poly, i)``).

        Candidate spots are tried in order: inside the target (closed shape with
        a faint fill, only if it fits with margin), then the side facing away
        from the diagram, then right, left, above, below. A spot is rejected if
        it covers other text, crosses a line, sits on a filled shape or leaves
        the label area. ``prefer`` ("inside", "outside", "right", "left",
        "above", "below") moves that choice to the front. The size is 28 (or
        ``size``), dropping to 22 if nothing fits; never smaller. If still
        nothing fits, the least bad spot is used and a finding is recorded.
        Returns the label, not yet on screen.
        """
        pref = normalize_prefer(prefer)
        if prefer is not None and pref is None:
            self.note("label %r: unknown prefer=%r, ignored" % (text, prefer))
        info = target_info(on)
        if info is None:
            self.note(
                "label %r: target has no geometry; placed at the centre" % (text,)
            )
            mob = make_text(text, LABEL_FONT, color, tex)
            mob.move_to([CX, 0, 0])
            self.register_text(mob)
            return mob
        ks = self.ks()
        if hasattr(on, "get_family") and not is_text_like(on):
            self.register(on)
        shapes = shape_infos([s[0] for s in ks["shapes"]])
        texts = self.text_boxes()
        dbox = self.diagram_box()
        wanted = snap_font(size if size is not None else LABEL_FONT)
        sizes = [wanted] + ([MIN_FONT] if wanted > MIN_FONT else [])
        least = None
        for font in sizes:
            mob = make_text(text, font, color, tex)
            w, h = mob.get_width(), mob.get_height()
            cands = label_candidates(info, w, h, LABEL_BUFF, pref, dbox)
            for cand in cands:
                inside = cand[0] == "inside"
                v = candidate_violations(
                    cand[1:], w, h, shapes, texts, info["idents"], inside
                )
                if is_good(v):
                    return self.place_label(mob, cand)
                bad = badness(v)
                if font == sizes[-1] and (least is None or bad < least[0]):
                    least = (bad, mob, cand, v)
        if least is None:
            mob = make_text(text, MIN_FONT, color, tex)
            self.note("label %r: no candidate spots; placed at the target" % (text,))
            mob.move_to([box_center(info["box"])[0], box_center(info["box"])[1], 0])
            self.register_text(mob)
            return mob
        _bad, mob, cand, v = least
        self.note(
            "label %r: no clear spot, used %s which %s"
            % (text, cand[0], violation_note(v))
        )
        return self.place_label(mob, cand)

    def place_label(self, mob, cand):
        mob.move_to([cand[1], cand[2], 0])
        self.register_text(mob)
        return mob

    # -- end of scene ------------------------------------------------------

    def leftovers(self):
        """Mobjects on screen that were left half-faded."""
        ks = self.ks()
        on_screen = set()
        found = []
        for top in self.mobjects:
            for m in top.get_family():
                on_screen.add(id(m))
        seen = set()
        for top in self.mobjects:
            stack = [top]
            while stack:
                m = stack.pop()
                if id(m) in seen:
                    continue
                seen.add(id(m))
                if is_text_like(m):
                    if m.has_points() or any(s.has_points() for s in m.get_family()):
                        if (
                            0.05
                            < array_max(
                                [fill_of(s) for s in m.get_family() if s.has_points()]
                            )
                            < 0.9
                        ):
                            found.append(m)
                    continue
                stack.extend(m.submobjects)
        for mob, intent in ks["shapes"]:
            if id(mob) in on_screen:
                now = max(fill_of(mob), stroke_of(mob))
                if 0.05 < now < 0.7 * intent:
                    found.append(mob)
        return found

    def finish(self):
        """Fade out anything left at partial opacity (once). Runs at tear-down.

        Costs 0.25 s only when something was left over, after the last cue.
        """
        ks = self.ks()
        if ks["finished"]:
            return 0.0
        ks["finished"] = True
        left = self.leftovers()
        if not left:
            return 0.0
        self.note("%d mobject(s) were left half-faded; faded out" % len(left))
        self.play(*[m.animate.set_opacity(0) for m in left], run_time=FADE_OUT_TIME)
        return FADE_OUT_TIME

    def audit(self):
        """Problems visible right now: text or shapes outside the safe area, half-faded text."""
        problems = []
        for t in self.ks()["texts"]:
            b = box_of(t)
            if b is not None and share_outside(b, SAFE) > 0.01:
                problems.append("text outside the safe area at %s" % (b,))
        for mob, _intent in self.ks()["shapes"]:
            b = box_of(mob)
            if b is not None and share_outside(b, SAFE) > 0.01:
                problems.append("shape outside the safe area at %s" % (b,))
        for m in self.leftovers():
            problems.append("half-faded %s" % m.__class__.__name__)
        return problems

    def tear_down(self):
        try:
            self.finish()
        except Exception as exc:
            self.note("finish failed: %s" % exc)
        super().tear_down()
