"""Unit tests for the render-side text overlap probe (#97).

Everything here uses lightweight fake mobjects; manimlib is not needed. The
probe module is self-contained (it runs inside the manimgl child), so it is
imported directly.
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from manimgen.probes import overlap_probe as probe
from manimgen.probes import overlap_report

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake mobjects. Class names matter: the probe recognises text by MRO names.
# ---------------------------------------------------------------------------


class FakeMob:
    def __init__(self, box=None, opacity=1.0, submobjects=(), fixed=False):
        self._box = box
        self._opacity = opacity
        self.submobjects = list(submobjects)
        self._fixed = fixed

    def get_family(self):
        out = [self]
        for sub in self.submobjects:
            out.extend(sub.get_family())
        return out

    def family_members_with_points(self):
        return [m for m in self.get_family() if m.has_points()]

    def has_points(self):
        return self._box is not None

    def get_fill_opacities(self):
        return [self._opacity] if self._box is not None else []

    def get_bounding_box(self):
        boxes = [m._box for m in self.get_family() if m._box is not None]
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        return [[x0, y0, 0], [(x0 + x1) / 2, (y0 + y1) / 2, 0], [x1, y1, 0]]

    def is_fixed_in_frame(self):
        return self._fixed


class VGroup(FakeMob):
    pass


class Rectangle(FakeMob):
    pass


class SurroundingRectangle(Rectangle):
    pass


class StringMobject(FakeMob):
    pass


class Text(StringMobject):
    def __init__(self, text, box, **kw):
        super().__init__(box=box, **kw)
        self.text = text


class Tex(StringMobject):
    def __init__(self, tex, box, **kw):
        super().__init__(box=box, **kw)
        self.tex_string = tex


class Brace(Tex):
    pass


class DecimalNumber(FakeMob):
    def __init__(self, num, box, **kw):
        super().__init__(box=box, **kw)
        self.num_string = num


class Integer(DecimalNumber):
    pass


class Frame:
    def get_width(self):
        return 14.2

    def get_height(self):
        return 8.0

    def get_center(self):
        return [0.0, 0.0, 0.0]

    def get_euler_angles(self):
        return [0.0, 0.0, 0.0]


class FakeScene:
    def __init__(self, *mobjects, time=0.0):
        self.mobjects = list(mobjects)
        self.frame = Frame()
        self.time = time


def _item(text, box, ident=0, ancestors=()):
    return probe.TextItem(text=text, box=box, ident=ident, ancestors=ancestors)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_overlap_fraction_uses_the_smaller_box():
    big = (0.0, 0.0, 4.0, 1.0)
    small = (1.0, 0.0, 2.0, 1.0)  # fully inside big
    assert probe.overlap_fraction(big, small) == pytest.approx(1.0)
    half = (3.5, 0.0, 4.5, 1.0)  # half of it inside big
    assert probe.overlap_fraction(big, half) == pytest.approx(0.5)


def test_disjoint_and_touching_boxes_do_not_overlap():
    a = (0.0, 0.0, 1.0, 1.0)
    assert probe.overlap_fraction(a, (2.0, 0.0, 3.0, 1.0)) == 0.0
    assert probe.overlap_fraction(a, (1.0, 0.0, 2.0, 1.0)) == 0.0  # shared edge


def test_degenerate_box_has_no_overlap():
    assert probe.overlap_fraction((0, 0, 0, 1), (0, 0, 1, 1)) == 0.0


def test_pair_above_threshold_is_reported():
    items = [_item("alpha", (0, 0, 2, 1), 1), _item("beta", (0.5, 0, 2.5, 1), 2)]
    pairs = probe.overlapping_pairs(items, threshold=0.25)
    assert [(a.text, b.text) for a, b, _ in pairs] == [("alpha", "beta")]


def test_pair_below_threshold_is_not_reported():
    items = [_item("alpha", (0, 0, 2, 1), 1), _item("beta", (1.8, 0, 3.8, 1), 2)]
    assert probe.overlapping_pairs(items, threshold=0.25) == []


def test_ancestor_and_descendant_are_never_a_pair():
    parent = _item("label", (0, 0, 2, 1), ident=1)
    child = _item("lab", (0, 0, 1, 1), ident=2, ancestors=(1,))
    assert probe.overlapping_pairs([parent, child]) == []


def test_same_string_is_ignored():
    items = [_item("x = 1", (0, 0, 2, 1), 1), _item(" x  = 1", (0, 0, 2, 1), 2)]
    assert probe.overlapping_pairs(items) == []


def test_tiny_overlap_is_ignored_even_if_fraction_is_high():
    # Two tiny glyph boxes fully overlapping, but with negligible area.
    items = [
        _item("a", (0, 0, 0.1, 0.1), 1),
        _item("b", (0, 0, 0.1, 0.1), 2),
    ]
    assert probe.overlapping_pairs(items, min_area=0.02) == []


# ---------------------------------------------------------------------------
# Recorder: dedupe and caps
# ---------------------------------------------------------------------------


def test_recorder_reports_each_pair_once_with_first_time():
    rec = probe.OverlapRecorder()
    items = [_item("alpha", (0, 0, 2, 1), 1), _item("beta", (0, 0, 2, 1), 2)]
    rec.observe(1.0, items)
    rec.observe(2.0, list(reversed(items)))
    assert len(rec.findings) == 1
    assert rec.findings[0].time == 1.0
    assert rec.checks == 2


def test_recorder_caps_the_list_and_counts_the_rest():
    rec = probe.OverlapRecorder(max_findings=3)
    for k in range(6):
        items = [
            _item(f"a{k}", (0, 0, 2, 1), 1),
            _item(f"b{k}", (0, 0, 2, 1), 2),
        ]
        rec.observe(float(k), items)
    assert len(rec.findings) == 3
    assert rec.dropped == 3
    assert rec.report()["dropped"] == 3


def test_one_caption_over_a_whole_row_does_not_fill_the_list():
    rec = probe.OverlapRecorder()
    caption = _item("One comparison. Eight cards gone.", (0, 0, 10, 1), 99)
    row = [_item(str(n), (n, 0, n + 0.8, 1), n + 1) for n in range(9)]
    rec.observe(5.0, [caption, *row])
    assert len(rec.findings) == probe.MAX_PER_TEXT
    assert rec.dropped == 9 - probe.MAX_PER_TEXT


def test_long_strings_are_truncated_to_40_chars():
    long = "of professional programmers failed in two hours"
    rec = probe.OverlapRecorder()
    rec.observe(0.0, [_item(long, (0, 0, 5, 1), 1), _item("90%", (1, 0, 2, 1), 2)])
    assert len(rec.findings[0].a) <= 40 or len(rec.findings[0].b) <= 40
    texts = {rec.findings[0].a, rec.findings[0].b}
    assert all(len(t) <= 40 for t in texts)


def test_report_is_json_serialisable():
    rec = probe.OverlapRecorder()
    rec.observe(0.5, [_item("a b", (0, 0, 2, 1), 1), _item("c", (0, 0, 1, 1), 2)])
    data = json.loads(json.dumps(rec.report("S", None)))
    assert data["findings"][0]["time"] == 0.5
    assert data["probe_error"] is None


# ---------------------------------------------------------------------------
# Morph (TransformMatchingTex midpoint) score
# ---------------------------------------------------------------------------


def test_morph_collision_is_zero_for_separate_glyphs():
    glyphs = [(0, (0, 0, 1, 1), 1.0), (1, (2, 0, 3, 1), 1.0)]
    assert probe.morph_collision(glyphs) == 0.0


def test_morph_collision_ignores_glyphs_of_the_same_part():
    glyphs = [(0, (0, 0, 1, 1), 1.0), (0, (0, 0, 1, 1), 1.0)]
    assert probe.morph_collision(glyphs) == 0.0


def test_morph_collision_is_high_when_parts_pile_up():
    glyphs = [(0, (0, 0, 1, 1), 1.0), (1, (0, 0, 1, 1), 1.0), (2, (5, 0, 6, 1), 1.0)]
    assert probe.morph_collision(glyphs) == pytest.approx(2 / 3)


def test_morph_scores_are_diagnostic_only_by_default():
    rec = probe.OverlapRecorder()
    glyphs = [(0, (0, 0, 1, 1), 1.0), (1, (0, 0, 1, 1), 1.0)]
    rec.observe_morph(3.0, "lo + hi = 3", "mid = -7", glyphs)
    assert rec.findings == []
    assert rec.report()["morphs"][0]["collision"] == 1.0


def test_garbled_morph_becomes_a_finding_when_a_threshold_is_set():
    rec = probe.OverlapRecorder(morph_threshold=0.5)
    glyphs = [(0, (0, 0, 1, 1), 1.0), (1, (0, 0, 1, 1), 1.0)]
    rec.observe_morph(3.0, "lo + hi = 3", "mid = -7", glyphs)
    assert rec.findings and rec.findings[0].a_kind == "morph"
    clean = probe.OverlapRecorder(morph_threshold=0.5)
    clean.observe_morph(3.0, "a", "b", [(0, (0, 0, 1, 1), 1.0)])
    assert clean.findings == [] and clean.morphs[0]["collision"] == 0.0


# ---------------------------------------------------------------------------
# manimlib adapter with fake mobjects
# ---------------------------------------------------------------------------


def test_text_classes_are_recognised_and_shapes_are_not():
    assert probe.is_text_like(Text("a", (0, 0, 1, 1)))
    assert probe.is_text_like(Tex("x", (0, 0, 1, 1)))
    assert probe.is_text_like(Integer("3", (0, 0, 1, 1)))
    assert not probe.is_text_like(Brace("", (0, 0, 1, 1)))
    assert not probe.is_text_like(Rectangle((0, 0, 1, 1)))
    assert not probe.is_text_like(VGroup())


def test_collect_finds_text_inside_groups_with_ancestry():
    t = Text("hello", (0, 0, 2, 1))
    group = VGroup(submobjects=[t])
    items = probe.collect_text_items(FakeScene(group))
    assert [i.text for i in items] == ["hello"]
    assert items[0].ancestors == (id(group),)


def test_collect_does_not_descend_into_text():
    inner = Text("inner", (0, 0, 1, 1))
    outer = Tex("outer", (0, 0, 2, 1), submobjects=[inner])
    items = probe.collect_text_items(FakeScene(outer))
    assert [i.text for i in items] == ["outer"]


def test_invisible_text_is_skipped():
    faded = Text("gone", (0, 0, 2, 1), opacity=0.0)
    dimmed = Text("dimmed", (0, 0, 2, 1), opacity=0.25)
    items = probe.collect_text_items(FakeScene(faded, dimmed))
    assert [i.text for i in items] == ["dimmed"]


def test_text_entirely_off_screen_is_skipped():
    off = Text("off", (20, 20, 22, 21))
    edge = Text("edge", (6.5, 0, 8.5, 1))  # partly inside a 14.2-wide frame
    items = probe.collect_text_items(FakeScene(off, edge))
    assert [i.text for i in items] == ["edge"]


def test_text_on_a_box_or_highlight_is_not_flagged():
    label = Text("23", (0, 0, 0.5, 0.5))
    box = Rectangle((-0.2, -0.2, 0.7, 0.7))
    halo = SurroundingRectangle((-0.3, -0.3, 0.8, 0.8))
    scene = FakeScene(VGroup(submobjects=[box, label]), halo)
    rec = probe.OverlapRecorder()
    rec.observe(0.0, probe.collect_text_items(scene))
    assert rec.findings == []


def test_two_overlapping_texts_in_a_scene_are_flagged():
    scene = FakeScene(
        Text("90%", (-0.5, 0.0, 0.5, 0.6)),
        VGroup(submobjects=[Text("while lo <= hi:", (-1.5, 0.1, 1.5, 0.5))]),
    )
    rec = probe.OverlapRecorder()
    rec.observe(6.5, probe.collect_text_items(scene))
    assert len(rec.findings) == 1
    assert {rec.findings[0].a, rec.findings[0].b} == {"90%", "while lo <= hi:"}


def test_rotated_3d_camera_skips_world_text_but_keeps_fixed_text():
    class Rotated(Frame):
        def get_euler_angles(self):
            return [0.5, 1.0, 0.0]

    scene = FakeScene(
        Text("world", (0, 0, 1, 1)), Text("hud", (0, 0, 1, 1), fixed=True)
    )
    scene.frame = Rotated()
    assert [i.text for i in probe.collect_text_items(scene)] == ["hud"]


# ---------------------------------------------------------------------------
# Never break a render
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_state(monkeypatch):
    monkeypatch.setattr(
        probe,
        "_state",
        {"recorder": None, "scene": "", "error": None, "path": None},
    )
    return probe._state


def test_install_is_a_no_op_without_the_env_var(monkeypatch, clean_state):
    monkeypatch.delenv(probe.REPORT_ENV, raising=False)
    before = list(sys.meta_path)
    probe.install()
    assert sys.meta_path == before
    assert clean_state["path"] is None


def test_an_exception_while_observing_is_recorded_not_raised(clean_state, tmp_path):
    clean_state["path"] = str(tmp_path / "r.json")

    class Exploding:
        @property
        def mobjects(self):
            raise RuntimeError("boom")

    probe._observe(Exploding())  # must not raise
    probe.write_report()
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert "boom" in data["probe_error"]
    assert data["findings"] == []


def test_patch_wraps_scene_and_writes_the_report(clean_state, tmp_path):
    clean_state["path"] = str(tmp_path / "r.json")
    calls = []

    class Scene:
        def __init__(self):
            self.mobjects = [
                Text("alpha", (0, 0, 2, 1)),
                Text("beta", (0.2, 0, 2.2, 1)),
            ]
            self.frame = Frame()
            self.time = 4.0

        def post_play(self):
            calls.append("post_play")

        def tear_down(self):
            calls.append("tear_down")

        def begin_animations(self, animations):
            calls.append("begin")

    module = types.SimpleNamespace(Scene=Scene)
    probe.patch_scene_module(module)
    probe.patch_scene_module(module)  # idempotent
    scene = Scene()
    scene.begin_animations([])
    scene.post_play()
    scene.tear_down()
    assert calls == ["begin", "post_play", "tear_down"]
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["scene"] == "Scene"
    assert data["findings"][0]["time"] == 4.0
    assert {data["findings"][0]["a"], data["findings"][0]["b"]} == {"alpha", "beta"}


def test_report_write_failure_is_swallowed(clean_state, tmp_path):
    clean_state["path"] = str(tmp_path / "missing_dir" / "r.json")
    probe.write_report()  # must not raise


# ---------------------------------------------------------------------------
# Host side: environment and report parsing
# ---------------------------------------------------------------------------


def test_pythonpath_is_joined_with_os_pathsep(monkeypatch):
    # Windows uses ";" (drive letters contain ":"), POSIX uses ":". The
    # bootstrap path is faked per platform: the real one on a Windows runner
    # holds a drive letter, which the POSIX case would split.
    cases = (
        (";", "E:\\m\\bootstrap", "C:\\a", "D:\\b"),
        (":", "/m/bootstrap", "/a", "/b"),
    )
    for sep, boot, first, second in cases:
        monkeypatch.setattr(os, "pathsep", sep)
        monkeypatch.setattr(overlap_report, "bootstrap_dir", lambda boot=boot: boot)
        env = overlap_report.with_probe_env(
            {"PYTHONPATH": f"{first}{sep}{second}", "PATH": "x"}, "r.json"
        )
        parts = env["PYTHONPATH"].split(sep)
        assert parts == [boot, first, second]
        assert env[overlap_report.REPORT_ENV] == "r.json"
        assert env["PATH"] == "x"


def test_pythonpath_without_existing_value(monkeypatch):
    env = overlap_report.with_probe_env({}, "r.json")
    assert env["PYTHONPATH"] == overlap_report.bootstrap_dir()


def test_bootstrap_files_exist():
    boot = overlap_report.bootstrap_dir()
    assert os.path.isfile(os.path.join(boot, "sitecustomize.py"))
    assert os.path.isfile(os.path.join(os.path.dirname(boot), "overlap_probe.py"))


def test_report_env_names_match():
    assert overlap_report.REPORT_ENV == probe.REPORT_ENV


def test_read_report_missing_file_is_empty(tmp_path):
    report = overlap_report.read_report(tmp_path / "nope.json")
    assert report.overlaps == () and report.probe_error is None
    assert not report.present


def test_read_report_garbage_is_a_probe_error_not_an_overlap(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{not json", encoding="utf-8")
    report = overlap_report.read_report(path)
    assert report.overlaps == ()
    assert report.probe_error


def test_read_report_parses_findings_and_skips_malformed_ones(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            {
                "findings": [
                    {"time": 6.57, "a": "while", "b": "90%", "fraction": 0.4},
                    {"time": "x"},
                ],
                "probe_error": None,
            }
        ),
        encoding="utf-8",
    )
    report = overlap_report.read_report(path)
    assert report.overlaps == (overlap_report.Overlap(6.57, "while", "90%", 0.4),)


def test_issue_line_names_both_texts_time_and_a_fix():
    line = overlap_report.format_issue(overlap_report.Overlap(6.57, "a", "b", 0.4))
    assert line.startswith("OVERLAP:")
    assert "'a'" in line and "'b'" in line and "t=6.6s" in line
    assert "next_to" in line


def test_probe_can_be_disabled(monkeypatch):
    monkeypatch.setenv(overlap_report.DISABLE_ENV, "0")
    assert not overlap_report.probe_enabled()
    monkeypatch.delenv(overlap_report.DISABLE_ENV)
    assert overlap_report.probe_enabled()
