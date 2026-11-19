"""Off-schema LLM output and bad input must degrade, not crash.

Each test is a concrete reply shape that used to raise an unrelated error
(IndexError, KeyError, TypeError) deep in the pipeline, where it was reported
as a narration or network failure.
"""

import pytest

from manimgen import config
from manimgen.input.pdf_parser import PdfInputError, parse_pdf
from manimgen.planner import lesson_planner as lp
from manimgen.planner.cue_parser import parse_cues
from manimgen.utils import strip_fencing

pytestmark = pytest.mark.unit


def test_trailing_cue_marker_adds_no_out_of_range_index():
    clean, indices = parse_cues("Hello brave world. [CUE] Second part here. [CUE]")
    assert indices == [0, 3]
    assert all(i < len(clean.split()) for i in indices)


def test_only_cue_markers_keeps_the_opening_index():
    assert parse_cues("[CUE] [CUE]")[1] == [0]


def test_missing_titles_are_filled_in():
    plan = {"sections": [{"id": "intro", "narration": "Hello there."}]}
    lp.ensure_titles(plan, default_title="gradient descent")
    assert plan["title"] == "gradient descent"
    assert plan["sections"][0]["title"] == "intro"


def test_existing_titles_are_kept():
    plan = {"title": "T", "sections": [{"id": "a", "title": "A"}]}
    lp.ensure_titles(plan)
    assert plan["title"] == "T" and plan["sections"][0]["title"] == "A"


def test_three_sections_with_the_same_id_get_distinct_ids():
    plan = {"sections": [{"id": "intro"}, {"id": "intro"}, {"id": "intro"}]}
    ids = [s["id"] for s in lp._sanitize_section_ids(plan)["sections"]]
    assert len(set(ids)) == 3


@pytest.mark.parametrize("cues", [None, ["a string", "another"], "nonsense"])
def test_extract_cues_survives_malformed_cues(monkeypatch, cues):
    monkeypatch.setattr(lp, "_refill_cues_via_llm", lambda *a, **k: None)
    plan = {
        "title": "T",
        "sections": [
            {"id": "s1", "title": "S", "narration": "One two. [CUE] Three.", "cues": cues}
        ],
    }
    out = lp._extract_cues(plan)["sections"][0]
    assert len(out["cues"]) == 2
    assert all(isinstance(c["visual"], str) and c["visual"] for c in out["cues"])


@pytest.mark.parametrize("raw", ["```", "```python```", "```python"])
def test_strip_fencing_on_a_fence_only_reply(raw):
    assert strip_fencing(raw) == ""


def test_invalid_render_quality_is_rejected_when_config_loads(tmp_path, monkeypatch):
    import yaml

    with open(config.config_path(), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["rendering"]["quality"] = "1080p"
    bad = tmp_path / "config.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("MANIMGEN_CONFIG", str(bad))
    with pytest.raises(config.ConfigError, match="rendering.quality"):
        config.load()


def test_non_pdf_bytes_raise_a_pdf_input_error(tmp_path):
    fake = tmp_path / "notes.pdf"
    fake.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfInputError):
        parse_pdf(str(fake))
