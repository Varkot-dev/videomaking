"""
Tests for lesson_planner.research_topic() and _format_research_brief().

Zero LLM calls — mocks chat() to return canned JSON.
"""

import json
from unittest.mock import patch

import pytest

from manimgen.planner.lesson_planner import research_topic, _format_research_brief


SAMPLE_BRIEF = {
    "topic": "Gradient Descent",
    "prerequisites": ["derivatives", "cost functions"],
    "core_concepts": [
        {
            "name": "The gradient",
            "explanation": "Vector of partial derivatives pointing toward steepest increase.",
            "common_misconception": "Students think gradient points toward minimum — it points toward maximum.",
            "visual_opportunity": "3D surface with gradient arrow at a point",
        }
    ],
    "key_formulas": [
        {
            "name": "Update rule",
            "formula": "\\theta_{n+1} = \\theta_n - \\alpha \\nabla f(\\theta_n)",
            "explanation": "theta is current position, alpha is learning rate",
        }
    ],
    "worked_example": {
        "description": "Minimise f(x) = x^2 from x=3 with lr=0.1",
        "steps": ["f'(x) = 2x, at x=3: grad=6", "x_1 = 3 - 0.6 = 2.4"],
    },
    "failure_modes": [
        {
            "name": "Learning rate too large",
            "description": "Overshoots the minimum",
            "visual_opportunity": "Dot bouncing past minimum on parabola",
        }
    ],
    "real_world_connections": ["Training neural networks"],
    "section_suggestions": ["The problem", "The gradient", "The update rule"],
}


class TestResearchTopic:

    def test_returns_dict_on_success(self):
        with patch("manimgen.planner.lesson_planner.chat", return_value=json.dumps(SAMPLE_BRIEF)):
            result = research_topic("Gradient Descent")
        assert isinstance(result, dict)
        assert result["topic"] == "Gradient Descent"
        assert len(result["core_concepts"]) == 1

    def test_returns_empty_dict_on_bad_json(self):
        with patch("manimgen.planner.lesson_planner.chat", return_value="not valid JSON"):
            result = research_topic("Gradient Descent")
        assert result == {}

    def test_strips_markdown_fencing(self):
        fenced = f"```json\n{json.dumps(SAMPLE_BRIEF)}\n```"
        with patch("manimgen.planner.lesson_planner.chat", return_value=fenced):
            result = research_topic("Gradient Descent")
        assert result.get("topic") == "Gradient Descent"


class TestFormatResearchBrief:

    def test_empty_brief_returns_empty_string(self):
        assert _format_research_brief({}) == ""

    def test_contains_core_concept_name(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert "The gradient" in text

    def test_contains_misconception(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert "Misconception" in text

    def test_contains_formula(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert "Update rule" in text

    def test_contains_worked_example_steps(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert "x_1 = 3 - 0.6 = 2.4" in text

    def test_contains_section_suggestions(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert "The gradient" in text
        assert "The update rule" in text

    def test_wrapped_in_markers(self):
        text = _format_research_brief(SAMPLE_BRIEF)
        assert text.startswith("--- RESEARCH BRIEF ---")
        assert text.endswith("--- END RESEARCH BRIEF ---")

    def test_handles_missing_keys_in_core_concepts(self):
        brief = {
            "core_concepts": [
                {},
                {"explanation": "No name here"},
                {"name": "Valid concept", "explanation": "Has both keys"},
            ]
        }
        result = _format_research_brief(brief)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Valid concept" in result
