"""
Tests for manimgen/planner/lesson_planner.py

Tests the section cap, JSON fencing strip, and plan structure validation.
All LLM calls are mocked — zero API cost.
"""

import json
import pytest
from unittest.mock import patch

from manimgen.planner.lesson_planner import (
    _cap_sections,
    _escape_bad_backslashes,
    _is_valid_unicode_escape,
    _safe_json_loads,
    _safe_json_loads_any,
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


class TestSafeJsonLoadsDictGuard:
    """#38(a): _safe_json_loads is annotated -> dict and feeds callers that
    immediately call .get(). json.loads can legitimately return a list/str/int
    when the LLM ignores the schema. The guard must fail fast with a clear
    error rather than letting `result.get(...)` raise AttributeError far away.
    """

    def test_object_returns_dict(self):
        assert _safe_json_loads('{"title": "x"}') == {"title": "x"}

    def test_top_level_array_raises_valueerror(self):
        with pytest.raises(ValueError, match="JSON object"):
            _safe_json_loads('[{"index": 0}]')

    def test_top_level_scalar_raises_valueerror(self):
        with pytest.raises(ValueError, match="JSON object"):
            _safe_json_loads("42")

    def test_caller_get_would_have_crashed_without_guard(self):
        # Demonstrates the bug class: a raw list has no .get().
        with pytest.raises(ValueError):
            _safe_json_loads('["a", "b"]')

    def test_any_variant_passes_list_through(self):
        # _safe_json_loads_any is the lenient sibling used by _refill_cues_via_llm,
        # which intentionally accepts a top-level array.
        assert _safe_json_loads_any('[{"index": 0}]') == [{"index": 0}]

    def test_any_variant_tolerates_bare_latex_backslash(self):
        # A bare LaTeX backslash that is NOT a valid JSON escape (\a) still
        # parses via the escape-repair pass. (\t, \n etc. are valid JSON escapes
        # and would decode to control chars on the first attempt — \alpha is the
        # realistic LaTeX case that breaks json.loads without the repair.)
        raw = r'{"v": "\alpha"}'
        assert _safe_json_loads_any(raw) == {"v": r"\alpha"}


class TestUnicodeEscapeGuard:
    r"""#38(b): _escape_bad_backslashes must only pass `\u` through when it is
    followed by EXACTLY four hex digits. LaTeX like `\underbrace` / `\union`
    starts with `\u` too — leaving those unescaped makes json.loads choke on a
    malformed `\uXXXX`.
    """

    # Use "\\u..." literals so the test data is an explicit backslash + u + ...
    # text stream (NOT a Python-decoded unicode character).

    def test_valid_unicode_escape_detected(self):
        # The text  \ u 0 0 e 9  is a valid escape at index 0.
        assert _is_valid_unicode_escape("\\u00e9 rest", 0) is True

    def test_uppercase_hex_is_valid(self):
        assert _is_valid_unicode_escape("\\u00E9", 0) is True

    def test_non_hex_after_u_is_invalid(self):
        # \uZZZZ — Z is not hex.
        assert _is_valid_unicode_escape("\\uZZZZ", 0) is False

    def test_truncated_unicode_escape_is_invalid(self):
        assert _is_valid_unicode_escape("\\u12", 0) is False

    def test_latex_underbrace_gets_escaped_and_parses(self):
        # \underbrace must NOT be treated as a \uXXXX escape; it gets doubled
        # so the resulting JSON parses and round-trips the literal backslash.
        raw = r'{"v": "\underbrace{x}"}'
        fixed = _escape_bad_backslashes(raw)
        assert json.loads(fixed) == {"v": r"\underbrace{x}"}

    def test_real_unicode_escape_preserved(self):
        # A genuine é escape must survive untouched and decode to 'é'.
        raw = '{"v": "caf\\u00e9"}'
        fixed = _escape_bad_backslashes(raw)
        assert json.loads(fixed) == {"v": "café"}

    def test_latex_union_word_boundary_escaped(self):
        # \union (LaTeX-ish) starts with \u but is not a 4-hex escape.
        raw = r'{"v": "A \union B"}'
        fixed = _escape_bad_backslashes(raw)
        assert json.loads(fixed) == {"v": r"A \union B"}
