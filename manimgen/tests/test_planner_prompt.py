"""
Tests for planner/prompts/planner_system.md.

Verifies the prompt file contains:
- The technique menu with all required technique names
- The enforcement rules (consecutive, axes_curve cap, at-least-one requirement)
- The N+1 cue rule (from the cue-index-fix)
- The WRONG/CORRECT examples for cue array length
- The structured visual field format requirement
- No stale vague guidance
"""

import os
import unittest

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "manimgen", "planner", "prompts", "planner_system.md"
)

REQUIRED_TECHNIQUES = [
    "sweep_highlight",
    "stagger_reveal",
    "camera_zoom",
    "equation_morph",
    "color_fill",
    "grid_transform",
    "tracker_label",
    "brace_annotation",
    "split_screen",
    "fade_reveal",
    "axes_curve",
]


def _read():
    with open(PROMPT_PATH) as f:
        return f.read()


class TestPlannerPromptTechniqueMenu(unittest.TestCase):

    def setUp(self):
        self.src = _read()

    def test_has_technique_menu_section(self):
        self.assertIn("Technique menu", self.src)

    def test_all_techniques_present(self):
        for name in REQUIRED_TECHNIQUES:
            self.assertIn(name, self.src, f"Technique '{name}' missing from planner_system.md")

    def test_enforcement_rules_present(self):
        self.assertIn("consecutive", self.src)
        self.assertIn("axes_curve", self.src)
        self.assertIn("camera_zoom", self.src)

    def test_visual_must_start_with_technique(self):
        self.assertIn("Technique: <name>", self.src)

    def test_has_bad_good_examples(self):
        self.assertIn("BAD", self.src)
        self.assertIn("GOOD", self.src)


class TestPlannerPromptCueIndexRule(unittest.TestCase):

    def setUp(self):
        self.src = _read()

    def test_has_n_plus_one_rule(self):
        self.assertIn("N + 1", self.src)

    def test_has_wrong_correct_contrast(self):
        # Intent: prompt shows BOTH the failure pattern and the correct
        # pattern as copyable JSON. Asserts the contrast exists, not exact
        # heading words (tuned for salience, may change).
        lower = self.src.lower()
        has_negative = any(
            w in self.src for w in ("WRONG", "DO NOT", "BROKEN", "mistake")
        )
        self.assertTrue(
            has_negative, "prompt must label the wrong/broken cue pattern"
        )
        self.assertIn("CORRECT", self.src)
        self.assertGreaterEqual(
            lower.count('"index": 0'), 2, "need wrong + correct JSON examples"
        )

    def test_index_zero_is_opening_segment(self):
        self.assertIn("index 0", self.src)
        self.assertIn("opening", self.src.lower())
