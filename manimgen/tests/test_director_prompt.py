"""
Tests for generator/prompts/director_system.md.

Verifies the prompt file contains:
- The mandatory technique diversity rule
- All required technique names in the Technique Reference section
- The exact API idioms each technique depends on
"""

import os
import unittest

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "manimgen", "generator", "prompts", "director_system.md"
)

REQUIRED_TECHNIQUES = [
    "camera_zoom",
    "equation_morph",
    "stagger_reveal",
    "sweep_highlight",
    "color_fill",
    "grid_transform",
    "tracker_label",
    "brace_annotation",
    "split_screen",
    "fade_reveal",
]

REQUIRED_API_SNIPPETS = [
    "self.frame.animate",          # camera_zoom
    "TransformMatchingTex(",       # equation_morph
    "LaggedStart(",                # stagger_reveal
    "SurroundingRectangle(",       # sweep_highlight
    "get_area(",                   # color_fill
    "apply_matrix(",               # grid_transform
    "always_redraw(",              # tracker_label
    "Brace(",                      # brace_annotation
    "to_edge(LEFT",                # split_screen
    "FlashAround(",                # fade_reveal
]


def _read_prompt():
    with open(PROMPT_PATH) as f:
        return f.read()


class TestDirectorPromptTechniqueSection(unittest.TestCase):

    def setUp(self):
        self.src = _read_prompt()

    def test_has_technique_reference_section(self):
        self.assertIn("Cinematic Technique Reference", self.src)

    def test_has_mandatory_diversity_rule(self):
        self.assertIn("at least 2", self.src)
        self.assertIn("failure", self.src)

    def test_all_technique_names_present(self):
        for name in REQUIRED_TECHNIQUES:
            self.assertIn(
                name, self.src,
                f"Technique '{name}' missing from director_system.md",
            )

    def test_all_api_snippets_present(self):
        for snippet in REQUIRED_API_SNIPPETS:
            self.assertIn(
                snippet, self.src,
                f"API snippet '{snippet}' missing from director_system.md",
            )

    def test_no_manimcommunity_in_technique_examples(self):
        # Extract only the Technique Reference section to avoid false positives
        # from the BANNED section which deliberately names these to warn against them.
        marker = "## Cinematic Technique Reference"
        if marker not in self.src:
            self.skipTest("Technique Reference section not found")
        technique_section = self.src.split(marker, 1)[1]
        banned = ["MathTex(", "self.camera.frame", "x_length=", "y_length="]
        for b in banned:
            self.assertNotIn(
                b, technique_section,
                f"Banned ManimCommunity API '{b}' found in Technique Reference examples",
            )
