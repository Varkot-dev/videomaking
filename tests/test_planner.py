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
        assert _MAX_SECTIONS_TOPIC == 8

    def test_pdf_cap_constant_is_10(self):
        assert _MAX_SECTIONS_PDF == 10


# ── plan_lesson (mocked LLM) ──────────────────────────────────────────────────

class TestPlanLesson:

    def _make_llm_response(self, n_sections: int) -> str:
        sections = [
            {
                "id": f"section_{i:02d}",
                "title": f"Section {i}",
                "narration": "Some narration text here.",
                "visual_description": "Show things on screen.",
                "key_objects": ["obj1"],
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
