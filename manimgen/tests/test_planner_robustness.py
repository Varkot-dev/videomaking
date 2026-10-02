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


# ── R22 ──────────────────────────────────────────────────────────────────────

_NARR = "Imagine a sorted list of numbers. [CUE] Now we search it quickly. [CUE] Done in few steps."


def _good_plan(n=2):
    return {
        "title": "T",
        "sections": [
            {
                "id": f"section_{i:02d}",
                "title": f"S{i}",
                "narration": _NARR,
                "cues": [
                    {"index": 0, "visual": "Technique: stagger_reveal. Boxes appear."},
                    {"index": 1, "visual": "Technique: sweep_highlight. A scan."},
                    {"index": 2, "visual": "Technique: fade_reveal. Clear all."},
                ],
            }
            for i in range(1, n + 1)
        ],
    }


def _run_with_critic(critic_reply):
    """Run plan_lesson with a valid planner reply and the given critic reply."""
    replies = [json.dumps(_good_plan()), critic_reply]
    with (
        patch(f"{PL}.research_topic", return_value={}),
        patch(f"{PL}.chat", side_effect=replies) as mock_chat,
    ):
        plan = plan_lesson("x")
    return plan, mock_chat


def _mutated(fn):
    plan = _good_plan()
    fn(plan)
    return json.dumps(plan)


def _drop_section(p):
    del p["sections"][1]


def _rename_id(p):
    p["sections"][0]["id"] = "intro"


def _shorten(p):
    p["sections"][0]["narration"] = "Short. [CUE] Tiny. [CUE] End."


def _unknown_technique(p):
    p["sections"][0]["cues"][0]["visual"] = "Technique: axes_build. Axes appear."


def _drop_a_cue(p):
    del p["sections"][0]["cues"][2]


def _drop_all_cues(p):
    del p["sections"][0]["cues"]


class TestCriticValidation:
    @pytest.mark.parametrize(
        "reply",
        [
            "{}",
            json.dumps({"error": "I cannot help with that"}),
            json.dumps({"title": "x"}),
            json.dumps({"title": "x", "sections": []}),
            json.dumps({"title": "x", "sections": "none"}),
            json.dumps([1, 2]),
            "not json at all",
            _mutated(_drop_section),
            _mutated(_rename_id),
            _mutated(_shorten),
            _mutated(_unknown_technique),
            _mutated(_drop_a_cue),
            _mutated(_drop_all_cues),
        ],
        ids=[
            "empty-object",
            "error-object",
            "no-sections-key",
            "empty-sections",
            "sections-not-list",
            "array",
            "prose",
            "fewer-sections",
            "changed-id",
            "narration-gutted",
            "unknown-technique",
            "cue-count-vs-markers",
            "cues-dropped",
        ],
    )
    def test_bad_critic_reply_keeps_original(self, reply, caplog):
        with caplog.at_level(logging.WARNING, logger=PL):
            plan, mock_chat = _run_with_critic(reply)
        assert [s["id"] for s in plan["sections"]] == ["section_01", "section_02"]
        assert plan["sections"][0]["narration"].startswith("Imagine a sorted list")
        assert mock_chat.call_count == 2  # plan + critic, no refill needed
        assert any("critic" in r.getMessage().lower() for r in caplog.records)

    def test_valid_improvement_is_accepted(self):
        def improve(p):
            p["title"] = "Better title"
            p["sections"][0]["cues"][0]["visual"] = (
                "Technique: stagger_reveal. Ten grey boxes appear one by one."
            )
            p["sections"][0]["narration"] = _NARR + " Binary search halves the range."

        plan, _ = _run_with_critic(_mutated(improve))
        assert plan["title"] == "Better title"
        assert "Ten grey boxes" in plan["sections"][0]["cues"][0]["visual"]

    def test_critic_may_fix_a_cue_count_the_planner_got_wrong(self):
        bad = _good_plan()
        del bad["sections"][0]["cues"][2]
        replies = [json.dumps(bad), json.dumps(_good_plan())]
        with (
            patch(f"{PL}.research_topic", return_value={}),
            patch(f"{PL}.chat", side_effect=replies) as mock_chat,
        ):
            plan = plan_lesson("x")
        assert len(plan["sections"][0]["cues"]) == 3
        assert mock_chat.call_count == 2

    def test_planner_unknown_technique_is_not_blamed_on_critic(self):
        orig = _good_plan()
        orig["sections"][0]["cues"][0]["visual"] = "Technique: made_up. x"
        improved = json.loads(json.dumps(orig))
        improved["title"] = "Better"
        with (
            patch(f"{PL}.research_topic", return_value={}),
            patch(f"{PL}.chat", side_effect=[json.dumps(orig), json.dumps(improved)]),
        ):
            assert plan_lesson("x")["title"] == "Better"

    def test_critic_prompt_lists_only_menu_techniques(self):
        from manimgen.planner.lesson_planner import (
            _load_critic_system_prompt,
            _technique_menu,
        )

        menu = _technique_menu()
        assert {"stagger_reveal", "camera_flythrough", "3d_surface"} <= menu
        prompt = _load_critic_system_prompt()
        for name in menu:
            assert name in prompt
        for stale in ("axes_build", "tex_reveal", "graph_trace", "parametric_surface"):
            assert stale not in prompt
        assert "{{" not in prompt


class TestPlanEntryGuard:
    @pytest.mark.parametrize(
        "bad",
        [
            "{}",
            json.dumps({"title": "x"}),
            json.dumps({"sections": []}),
            json.dumps({"sections": ["a"]}),
            json.dumps({"sections": [{"id": "s1", "title": "t"}]}),
            json.dumps({"sections": [{"id": "s1", "narration": "   "}]}),
        ],
    )
    def test_bad_plan_reasks_once_then_clear_error(self, bad):
        with (
            patch(f"{PL}.research_topic", return_value={}),
            patch(f"{PL}.chat", side_effect=[bad, bad]) as mock_chat,
        ):
            with pytest.raises(ValueError, match="sections"):
                plan_lesson("x")
        assert mock_chat.call_count == 2

    def test_bad_then_good_recovers(self):
        with (
            patch(f"{PL}._self_correct", side_effect=lambda p, *a, **k: p),
            patch(f"{PL}.research_topic", return_value={}),
            patch(f"{PL}.chat", side_effect=["{}", json.dumps(_good_plan())]),
        ):
            assert len(plan_lesson("x")["sections"]) == 2
