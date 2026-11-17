"""Off-screen and safe-area findings from the in-render probe.

The geometry and the recorder are tested here without manimlib. The real
render check lives in test_overlap_probe_render.py (needs a display).
"""

import pytest

from manimgen import paths
from manimgen.probes import overlap_probe as probe
from manimgen.probes import overlap_report as report

pytestmark = pytest.mark.unit

# A vertical short: 4.5 x 8 units.
FRAME = (-2.25, -4.0, 2.25, 4.0)
MARGINS = paths.short_safe_margins()


def _item(text, box):
    return probe.TextItem(text=text, box=box, rect=FRAME, ident=id(text))


def _kinds(recorder):
    return {(f.a, f.kind) for f in recorder.findings}


def test_safe_box_matches_the_documented_coordinates():
    xmin, ymin, xmax, ymax = probe.inset_rect(FRAME, MARGINS)
    assert (xmin, ymin, xmax, ymax) == pytest.approx((-2.05, -2.0, 1.45, 2.8))


def test_safe_box_scales_with_a_zoomed_camera():
    zoomed = tuple(v * 0.8 for v in FRAME)
    xmin, ymin, xmax, ymax = probe.inset_rect(zoomed, MARGINS)
    assert (xmin, ymin, xmax, ymax) == pytest.approx(
        (-2.05 * 0.8, -2.0 * 0.8, 1.45 * 0.8, 2.8 * 0.8)
    )


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ((-1.0, 0.0, 1.0, 1.0), 0.0),
        ((1.25, 0.0, 3.25, 1.0), 0.5),
        ((3.0, 0.0, 4.0, 1.0), 1.0),
    ],
)
def test_outside_fraction(box, expected):
    assert probe.outside_fraction(box, FRAME) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw", [None, "", "a,b,c,d", "0.1,0.1,0.1", "0.6,0,0,0", "-1,0,0,0"]
)
def test_bad_margins_are_ignored(raw):
    assert probe.parse_margins(raw) is None


def test_margins_round_trip_through_the_environment():
    env = report.with_probe_env({}, "r.json", MARGINS)
    assert probe.parse_margins(env[report.SAFE_MARGINS_ENV]) == pytest.approx(
        MARGINS, abs=1e-4
    )
    assert report.SAFE_MARGINS_ENV == probe.SAFE_MARGINS_ENV


def test_landscape_render_does_not_inherit_a_stale_safe_area():
    env = report.with_probe_env({report.SAFE_MARGINS_ENV: "0.1,0.1,0.1,0.1"}, "r.json")
    assert report.SAFE_MARGINS_ENV not in env


def test_recorder_reports_offscreen_and_unsafe_text():
    rec = probe.OverlapRecorder()
    items = [
        _item("fine", (-1.0, 0.5, 0.6, 1.0)),
        _item("too wide", (-3.0, 2.0, 3.0, 2.5)),
        _item("under caption", (-1.2, -2.8, 0.6, -2.4)),
        _item("under buttons", (1.2, 0.3, 2.2, 0.7)),
    ]
    rec.observe_bounds(1.0, items, MARGINS)
    assert _kinds(rec) == {
        ("too wide", "offscreen"),
        ("under caption", "unsafe"),
        ("under buttons", "unsafe"),
    }


def test_without_safe_margins_only_offscreen_is_reported():
    rec = probe.OverlapRecorder()
    rec.observe_bounds(
        1.0,
        [
            _item("under caption", (-1.2, -2.8, 0.6, -2.4)),
            _item("cut", (1.5, 0, 3.0, 0.5)),
        ],
        None,
    )
    assert _kinds(rec) == {("cut", "offscreen")}


def test_small_overshoots_are_tolerated():
    rec = probe.OverlapRecorder()
    # 4% of the box past the frame edge; 10% past the safe edge.
    rec.observe_bounds(1.0, [_item("edge", (1.25, 0.0, 2.29, 0.5))], None)
    rec.observe_bounds(1.0, [_item("near", (0.45, 0.0, 1.56, 0.5))], MARGINS)
    assert rec.findings == []


def test_same_defect_is_reported_once_across_frames():
    rec = probe.OverlapRecorder()
    for t in (1.0, 2.0, 3.0):
        rec.observe_bounds(t, [_item("too wide", (-3.0, 2.0, 3.0, 2.5))], MARGINS)
    assert len(rec.findings) == 1 and rec.findings[0].time == 1.0


def test_findings_survive_the_report_and_become_specific_issues():
    rec = probe.OverlapRecorder()
    rec.observe_bounds(
        1.0,
        [
            _item("too wide", (-3.0, 2.0, 3.0, 2.5)),
            _item("low", (-1.2, -2.8, 0.6, -2.4)),
        ],
        MARGINS,
    )
    parsed = report._parse(rec.report("S"))
    assert [o.kind for o in parsed.overlaps] == ["offscreen", "unsafe"]
    cut, unsafe = report.overlap_issues(parsed.overlaps)
    assert cut.startswith(report.ISSUE_PREFIX) and "cut off by the edge" in cut
    assert "safe area" in unsafe and "x -2.0 to 1.4" in unsafe


def test_reports_without_a_kind_are_read_as_overlaps():
    parsed = report._parse(
        {"findings": [{"time": 1, "a": "x", "b": "y", "fraction": 0.5}]}
    )
    assert parsed.overlaps[0].kind == "overlap"
    assert "drawn on top of" in report.format_issue(parsed.overlaps[0])
