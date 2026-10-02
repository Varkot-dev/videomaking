"""Planner robustness tests (R18 parsing and re-ask, R22 critic validation,
R23 PDF schema and visible truncation).

Leaf seam: ``chat`` is mocked. Zero API cost.
"""

import json
import logging
from unittest.mock import patch

import pytest

from manimgen.planner.lesson_planner import plan_lesson, plan_lesson_from_pdf

PL = "manimgen.planner.lesson_planner"


def _plan(visual="Technique: fade_reveal. Tex(§frac{1}{x}) appears.", n=1, raw=False):
    """Plan JSON. ``raw=True`` splices the visual in WITHOUT JSON escaping, the
    way a model emits LaTeX with bare backslashes."""
    out = json.dumps(
        {
            "title": "T",
            "sections": [
                {
                    "id": f"section_{i:02d}",
                    "title": f"S{i}",
                    "narration": "One two three four five.",
                    "cues": [{"index": 0, "visual": "@@V@@" if raw else visual}],
                }
                for i in range(1, n + 1)
            ],
        }
    )
    return out.replace("@@V@@", visual) if raw else out


# ── R18 ──────────────────────────────────────────────────────────────────────


@patch(f"{PL}._self_correct", side_effect=lambda p, *a, **k: p)
@patch(f"{PL}.research_topic", return_value={})
@patch(f"{PL}.chat")
class TestPlanJsonRobustness:
    def test_prose_before_fenced_json(self, mock_chat, _r, _c):
        mock_chat.return_value = "Here is the plan:\n```json\n" + _plan() + "\n```"
        assert plan_lesson("x")["title"] == "T"
        assert mock_chat.call_count == 1

    def test_prose_after_json(self, mock_chat, _r, _c):
        mock_chat.return_value = _plan() + "\n\nHope this helps!"
        assert plan_lesson("x")["title"] == "T"
        assert mock_chat.call_count == 1

    def test_brace_in_leading_prose(self, mock_chat, _r, _c):
        mock_chat.return_value = "Here is the {plan} you wanted:\n" + _plan()
        assert plan_lesson("x")["title"] == "T"

    def test_latex_backslashes_survive(self, mock_chat, _r, _c):
        mock_chat.return_value = _plan(
            r"Technique: equation_morph. Tex(\frac{1}{x}) then \theta and \nabla f \right)",
            raw=True,
        )
        visual = plan_lesson("x")["sections"][0]["cues"][0]["visual"]
        assert r"\frac{1}{x}" in visual
        assert r"\theta" in visual
        assert r"\nabla f" in visual
        assert r"\right)" in visual
        assert "\x0c" not in visual and "\t" not in visual

    def test_prose_and_raw_latex_together(self, mock_chat, _r, _c):
        mock_chat.return_value = (
            "Sure!\n"
            + _plan(r"Technique: fade_reveal. \beta and \text{x}", raw=True)
            + "\nBye"
        )
        visual = plan_lesson("x")["sections"][0]["cues"][0]["visual"]
        assert r"\beta" in visual and r"\text{x}" in visual

    def test_already_escaped_backslash_not_double_escaped(self, mock_chat, _r, _c):
        mock_chat.return_value = (
            '{"title": "T", "sections": [{"id": "section_01", "title": "S", '
            '"narration": "One two three.", '
            '"cues": [{"index": 0, "visual": "Tex(\\\\frac{1}{x})"}]}]}'
        )
        visual = plan_lesson("x")["sections"][0]["cues"][0]["visual"]
        assert visual == r"Tex(\frac{1}{x})"

    def test_legit_newline_escape_preserved(self, mock_chat, _r, _c):
        mock_chat.return_value = (
            '{"title": "T", "sections": [{"id": "section_01", "title": "S", '
            '"narration": "Line one.\\nNext line here.", '
            '"cues": [{"index": 0, "visual": "v"}]}]}'
        )
        narration = plan_lesson("x")["sections"][0]["narration"]
        assert "Line one." in narration and "Next line here." in narration

    def test_reask_once_then_succeeds(self, mock_chat, _r, _c):
        mock_chat.side_effect = ["no json at all", _plan()]
        assert plan_lesson("x")["title"] == "T"
        assert mock_chat.call_count == 2
        second = mock_chat.call_args_list[1].kwargs
        assert "could not be used" in second["user"]
        assert second["json_mode"] is True

    def test_reask_is_bounded_to_one(self, mock_chat, _r, _c):
        mock_chat.side_effect = ["nope", "still nope", _plan()]
        with pytest.raises(ValueError):
            plan_lesson("x")
        assert mock_chat.call_count == 2

    @patch("manimgen.input.pdf_parser.parse_pdf")
    def test_pdf_path_same_tolerance_and_reask(self, mock_parse, mock_chat, _r, _c):
        mock_parse.return_value = {
            "raw_text": "notes",
            "chunks": ["notes"],
            "extracted_pages": 1,
            "images": ["img"],
        }
        mock_chat.side_effect = ["garbage", "Sure:\n" + _plan() + "\nthanks"]
        assert plan_lesson_from_pdf("n.pdf")["title"] == "T"
        assert mock_chat.call_count == 2
        assert mock_chat.call_args_list[1].kwargs["images"] == ["img"]
