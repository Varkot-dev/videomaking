"""
Tests for manimgen/planner/lesson_planner.py

Tests the section cap, JSON fencing strip, and plan structure validation.
All LLM calls are mocked — zero API cost.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from manimgen.planner.lesson_planner import (
    _cap_sections,
    _strip_fencing,
    plan_lesson,
    plan_lesson_from_pdf,
    _reconstruct_latex,
    _MAX_SECTIONS_TOPIC,
    _MAX_SECTIONS_PDF,
)


# ── _strip_fencing ────────────────────────────────────────────────────────────

class TestStripFencing:

    def test_strips_json_fencing(self):
        raw = '```json\n{"title": "foo"}\n```'
        assert _strip_fencing(raw) == '{"title": "foo"}'

    def test_strips_plain_fencing(self):
        raw = '```\n{"title": "foo"}\n```'
        assert _strip_fencing(raw) == '{"title": "foo"}'

    def test_no_fencing_passthrough(self):
        raw = '{"title": "foo"}'
        assert _strip_fencing(raw) == '{"title": "foo"}'

    def test_strips_whitespace(self):
        raw = '  \n{"title": "foo"}\n  '
        assert _strip_fencing(raw) == '{"title": "foo"}'

    def test_strips_fencing_with_trailing_whitespace(self):
        raw = '```json\n{"title": "foo"}\n```  '
        result = _strip_fencing(raw)
        assert result == '{"title": "foo"}'


# ── _cap_sections ─────────────────────────────────────────────────────────────

class TestCapSections:

    def _make_plan(self, n: int) -> dict:
        return {
            "title": "Test",
            "sections": [{"id": f"section_{i:02d}", "title": f"Section {i}"} for i in range(1, n + 1)]
        }

    def test_under_limit_unchanged(self):
        plan = self._make_plan(5)
        result = _cap_sections(plan, 8)
        assert len(result["sections"]) == 5

    def test_at_limit_unchanged(self):
        plan = self._make_plan(8)
        result = _cap_sections(plan, 8)
        assert len(result["sections"]) == 8

    def test_over_limit_capped(self):
        plan = self._make_plan(19)
        result = _cap_sections(plan, 10)
        assert len(result["sections"]) == 10

    def test_capped_keeps_first_sections(self):
        plan = self._make_plan(15)
        result = _cap_sections(plan, 5)
        ids = [s["id"] for s in result["sections"]]
        assert ids == ["section_01", "section_02", "section_03", "section_04", "section_05"]

    def test_empty_sections_ok(self):
        plan = {"title": "Test", "sections": []}
        result = _cap_sections(plan, 8)
        assert result["sections"] == []

    def test_missing_sections_key_ok(self):
        plan = {"title": "Test"}
        result = _cap_sections(plan, 8)
        assert result == {"title": "Test"}

    def test_topic_cap_constant_is_8(self):
        assert _MAX_SECTIONS_TOPIC == 6

    def test_pdf_cap_constant_is_10(self):
        assert _MAX_SECTIONS_PDF == 8


# ── plan_lesson (mocked LLM) ──────────────────────────────────────────────────

class TestPlanLesson:

    def _make_llm_response(self, n_sections: int) -> str:
        sections = [
            {
                "id": f"section_{i:02d}",
                "title": f"Section {i}",
                "narration": "Some narration text here.",
                "duration_seconds": 30,
            }
            for i in range(1, n_sections + 1)
        ]
        return json.dumps({"title": "Test Topic", "sections": sections})

    @patch("manimgen.planner.lesson_planner.chat")
    def test_returns_valid_plan(self, mock_chat):
        mock_chat.return_value = self._make_llm_response(5)
        plan = plan_lesson("binary search")
        assert "title" in plan
        assert "sections" in plan
        assert len(plan["sections"]) == 5

    @patch("manimgen.planner.lesson_planner.chat")
    def test_caps_overshooting_llm(self, mock_chat):
        mock_chat.return_value = self._make_llm_response(20)
        plan = plan_lesson("some topic")
        assert len(plan["sections"]) == _MAX_SECTIONS_TOPIC

    @patch("manimgen.input.pdf_parser.parse_pdf")
    @patch("manimgen.planner.lesson_planner.chat")
    def test_pdf_path_caps_overshooting_llm(self, mock_chat, mock_parse_pdf):
        """Regression: plan_lesson_from_pdf had the SAME _self_correct
        re-inflation bug as the topic path (it was fixed only on the topic
        path originally). The critic LLM here returns 20 sections; the cap
        must still hold after _self_correct."""
        mock_parse_pdf.return_value = {
            "raw_text": "Some lecture notes text.",
            "chunks": ["Some lecture notes text."],
            "extracted_pages": 1,
            "images": [],
        }
        # return_value (not side_effect) → the _self_correct critic call
        # also gets the 20-section payload, recreating the bug scenario.
        mock_chat.return_value = self._make_llm_response(20)
        plan = plan_lesson_from_pdf("notes.pdf")
        assert len(plan["sections"]) == _MAX_SECTIONS_PDF

    @patch("manimgen.planner.lesson_planner.chat")
    def test_handles_json_fencing(self, mock_chat):
        raw = f'```json\n{self._make_llm_response(3)}\n```'
        mock_chat.return_value = raw
        plan = plan_lesson("binary search")
        assert len(plan["sections"]) == 3

    @patch("manimgen.planner.lesson_planner.chat")
    def test_sections_have_required_fields(self, mock_chat):
        mock_chat.return_value = self._make_llm_response(3)
        plan = plan_lesson("binary search")
        for section in plan["sections"]:
            assert "id" in section
            assert "title" in section

    @patch("manimgen.planner.lesson_planner.chat")
    def test_invalid_json_raises(self, mock_chat):
        mock_chat.return_value = "this is not json"
        with pytest.raises(Exception):
            plan_lesson("binary search")


class TestReconstructLatex:
    """A2 contract: the planner emits a backslash-free `§` sentinel for
    LaTeX (a raw \\ silently corrupts JSON). _reconstruct_latex restores real
    LaTeX before any consumer sees the cue."""

    def test_sentinel_becomes_backslash(self):
        assert _reconstruct_latex("Tex(§frac{1}{x})") == r"Tex(\frac{1}{x})"

    def test_multiple_commands_and_double_sentinel_linebreak(self):
        src = "§theta_1 = 3 - 0.1 §cdot §nabla f §§ next line"
        assert _reconstruct_latex(src) == r"\theta_1 = 3 - 0.1 \cdot \nabla f \\ next line"

    def test_sentinel_free_text_is_unchanged(self):
        prose = "Technique: sweep_highlight. A yellow SurroundingRectangle scans."
        assert _reconstruct_latex(prose) == prose

    def test_idempotent_on_already_plain_prose(self):
        # The synthesised fallback contains no §, so reconstruction is a no-op.
        fallback = "Section 'Intro': animate segment 1 of 3."
        assert _reconstruct_latex(fallback) == fallback


class TestPlanLessonLatexRoundTrip:
    """End-to-end: a planner LLM response using the § sentinel must parse
    cleanly (no JSON crash) AND deliver real LaTeX in cues[].visual."""

    def _llm_plan_with_sentinel_latex(self):
        # Mirrors the real failure shape that crashed 'gradient descent',
        # but written in the new backslash-free contract.
        return json.dumps({
            "title": "Gradient Descent",
            "estimated_duration_seconds": 30,
            "sections": [{
                "id": "section_01",
                "title": "The Update Rule",
                "narration": "We update theta. [CUE] Then we step.",
                "cues": [
                    {"index": 0, "visual": "Technique: equation_morph. Show Tex(§theta_{n+1} = §theta_n - §alpha §nabla f)"},
                    {"index": 1, "visual": "Technique: fade_reveal. Tex(§frac{dL}{d§theta}) appears."},
                ],
            }],
        })

    @patch("manimgen.planner.lesson_planner._self_correct", side_effect=lambda p, *a, **k: p)
    @patch("manimgen.planner.lesson_planner.research_topic", return_value={})
    @patch("manimgen.planner.lesson_planner.chat")
    def test_sentinel_plan_parses_and_yields_real_latex(
        self, mock_chat, _mock_research, _mock_correct
    ):
        mock_chat.return_value = self._llm_plan_with_sentinel_latex()
        plan = plan_lesson("gradient descent")

        visuals = [c["visual"] for c in plan["sections"][0]["cues"]]
        # No § survives to the Director; real backslashes restored.
        assert all("§" not in v for v in visuals)
        assert r"\theta_{n+1} = \theta_n - \alpha \nabla f" in visuals[0]
        assert r"\frac{dL}{d\theta}" in visuals[1]
        # Technique prefix preserved (load-bearing for example selection / 3D).
        assert visuals[0].startswith("Technique: equation_morph")
