"""
Tests for the new Director architecture:
- Planner outputs storyboard cues[] field
- generate_scenes() produces a single-file scene with self.wait()
- CLI generates one scene per section
- codeguard precheck_and_autofix works on code strings
- cutter computes start times correctly
"""
import json
import os
import sys
import textwrap
import unittest
from unittest.mock import MagicMock, patch

# Ensure package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestPlannerStoryboardOutput(unittest.TestCase):
    """Planner _extract_cues() correctly populates section['cues']."""

    def test_extract_cues_creates_cues_list(self):
        from manimgen.planner.lesson_planner import _extract_cues

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Test",
                "narration": "First part. [CUE] Second part. [CUE] Third part.",
                "cues": [
                    {"index": 0, "visual": "Axes appear"},
                    {"index": 1, "visual": "Dot moves"},
                    {"index": 2, "visual": "Annotation"},
                ],
            }]
        }
        result = _extract_cues(plan)
        section = result["sections"][0]
        self.assertEqual(len(section["cues"]), 3)
        self.assertEqual(section["cues"][0]["visual"], "Axes appear")
        self.assertEqual(section["cues"][1]["index"], 1)

    def test_extract_cues_fills_missing_visuals(self):
        from manimgen.planner.lesson_planner import _extract_cues

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Test",
                "narration": "A. [CUE] B. [CUE] C.",
                # No cues[] provided — should be synthesised with non-empty fallbacks
            }]
        }
        result = _extract_cues(plan)
        section = result["sections"][0]
        self.assertEqual(len(section["cues"]), 3)
        self.assertEqual(section["cues"][0]["index"], 0)
        # Fallback visual must be non-empty — empty string means Director gets no brief
        self.assertTrue(len(section["cues"][0]["visual"]) > 0)
        self.assertTrue(len(section["cues"][1]["visual"]) > 0)
        self.assertTrue(len(section["cues"][2]["visual"]) > 0)

    def test_extract_cues_strips_cue_markers_from_narration(self):
        from manimgen.planner.lesson_planner import _extract_cues

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Test",
                "narration": "Hello world. [CUE] Goodbye.",
            }]
        }
        result = _extract_cues(plan)
        self.assertNotIn("[CUE]", result["sections"][0]["narration"])

    def test_undersupplied_cues_trigger_llm_refill_with_real_content(self):
        """The systemic bug: planner emits 1 cue per [CUE] marker (2) instead
        of one per segment (3). The refill must re-prompt and produce real
        per-segment visuals, NOT blank placeholders."""
        from manimgen.planner.lesson_planner import _extract_cues
        import json as _json

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Gradient",
                "narration": "Open idea. [CUE] Middle idea. [CUE] Close idea.",
                # 2 markers -> 3 segments, but planner supplied only 2 cues
                "cues": [
                    {"index": 0, "visual": "Technique: fade_reveal. middle viz"},
                    {"index": 1, "visual": "Technique: axes_curve. close viz"},
                ],
            }]
        }
        refilled = _json.dumps([
            {"index": 0, "visual": "Technique: fade_reveal. opening viz"},
            {"index": 1, "visual": "Technique: stagger_reveal. middle viz"},
            {"index": 2, "visual": "Technique: axes_curve. close viz"},
        ])
        with patch("manimgen.planner.lesson_planner.chat", return_value=refilled) as mock_chat:
            result = _extract_cues(plan)

        cues = result["sections"][0]["cues"]
        mock_chat.assert_called_once()  # exactly one targeted refill call
        self.assertEqual(len(cues), 3)
        # Every cue has real Technique content — no "animate segment N" placeholder
        for c in cues:
            self.assertIn("Technique:", c["visual"])
            self.assertNotIn("animate segment", c["visual"])

    def test_refill_failure_falls_back_gracefully_no_crash(self):
        """If the refill LLM call fails or returns the wrong count, the
        pipeline must NOT crash — it falls back to placeholder synthesis so
        a video still renders (degraded, but not a hard failure)."""
        from manimgen.planner.lesson_planner import _extract_cues

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Gradient",
                "narration": "Open. [CUE] Middle. [CUE] Close.",
                "cues": [
                    {"index": 0, "visual": "Technique: fade_reveal. a"},
                    {"index": 1, "visual": "Technique: axes_curve. b"},
                ],
            }]
        }
        with patch("manimgen.planner.lesson_planner.chat",
                   side_effect=Exception("LLM down")):
            result = _extract_cues(plan)  # must not raise

        cues = result["sections"][0]["cues"]
        self.assertEqual(len(cues), 3)  # still N+1, degraded
        self.assertTrue(all(len(c["visual"]) > 0 for c in cues))

    def test_extract_cues_word_indices_length_matches_cues(self):
        from manimgen.planner.lesson_planner import _extract_cues

        plan = {
            "sections": [{
                "id": "section_01",
                "title": "T",
                "narration": "One two three. [CUE] Four five. [CUE] Six.",
            }]
        }
        result = _extract_cues(plan)
        section = result["sections"][0]
        self.assertEqual(len(section["cue_word_indices"]), len(section["cues"]))

    def test_extract_cues_planner_off_by_one_synthesises_fallback(self):
        """Regression: planner gives N cues for N [CUE] markers instead of N+1.

        When the LLM omits the opening cue (index 0), _extract_cues must:
        - still produce N+1 entries (one per segment)
        - fill the missing trailing entry with a non-empty fallback visual
        - never pass visual: "" to the Director
        """
        from manimgen.planner.lesson_planner import _extract_cues

        # 2 [CUE] markers → 3 segments, but planner only gave 2 cues (wrong)
        plan = {
            "sections": [{
                "id": "section_01",
                "title": "Binary Search",
                "narration": "Opening sentence. [CUE] Middle sentence here. [CUE] Closing sentence.",
                "cues": [
                    {"index": 0, "visual": "Middle visual — planner started counting from first [CUE]"},
                    {"index": 1, "visual": "Closing visual"},
                ],
            }]
        }
        result = _extract_cues(plan)
        section = result["sections"][0]

        # Must have 3 cues, not 2
        self.assertEqual(len(section["cues"]), 3)
        # All visuals must be non-empty
        for cue in section["cues"]:
            self.assertTrue(len(cue["visual"]) > 0, f"cue {cue['index']} has empty visual")
        # Indices must be 0, 1, 2
        self.assertEqual([c["index"] for c in section["cues"]], [0, 1, 2])


class TestSceneGeneratorDirector(unittest.TestCase):
    """generate_scenes() produces valid Python with self.wait() for each cue."""

    def _make_section(self):
        return {
            "id": "section_01",
            "title": "Test Section",
            "narration": "Hello world.",
            "cue_word_indices": [0, 3],
            "cues": [
                {"index": 0, "visual": "A circle appears center screen, red, radius 1."},
                {"index": 1, "visual": "Text 'Done' fades in below the circle."},
            ],
        }

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_generates_single_file_with_wait(self, mock_paths, mock_chat):
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)

        mock_chat.return_value = textwrap.dedent("""
            from manimlib import *

            class Section01Scene(Scene):
                def construct(self):
                    # CUE 0
                    c = Circle(radius=1.0, color=RED)
                    self.play(ShowCreation(c), run_time=1.0)
                    self.wait(3.2)

                    # CUE 1
                    t = Text("Done", font_size=36)
                    self.play(FadeIn(t), run_time=1.0)
                    self.wait(2.1)

                    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
                    self.wait(0.5)
        """).strip()

        from manimgen.generator.scene_generator import generate_scenes
        section = self._make_section()
        code, class_name, scene_path = generate_scenes(
            section, cue_durations=[4.2, 3.1]
        )

        self.assertIn("from manimlib import *", code)
        self.assertIn("class Section01Scene", code)
        self.assertIn("self.wait(", code)
        self.assertEqual(class_name, "Section01Scene")

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_class_name_derived_from_section_id(self, mock_paths, mock_chat):
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = "from manimlib import *\n\nclass Section02Scene(Scene):\n    def construct(self):\n        self.wait(5.0)\n"

        from manimgen.generator.scene_generator import generate_scenes
        section = {
            "id": "section_02", "title": "S2",
            "narration": "hi.", "cue_word_indices": [0],
            "cues": [{"index": 0, "visual": "something"}],
        }
        _, class_name, _ = generate_scenes(section, cue_durations=[5.0])
        self.assertEqual(class_name, "Section02Scene")

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_3d_technique_promotes_scene_to_threedscene(self, mock_paths, mock_chat):
        """Regression guard (safety-review #1 risk): 3D promotion does a
        substring match for '3d_surface' in cues[].visual. The A2 backslash-
        free contract must NOT disturb how techniques are named in `visual`,
        or every 3D scene silently renders as a plain Scene and crashes at
        runtime with AttributeError on self.frame. This test fails loudly if
        that path regresses."""
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        # LLM returns a PLAIN Scene; promotion must rewrite it to ThreeDScene.
        mock_chat.return_value = (
            "from manimlib import *\n\n"
            "class Section03Scene(Scene):\n"
            "    def construct(self):\n"
            "        self.frame.reorient(-40, 70)\n"
            "        self.wait(5.0)\n"
        )

        from manimgen.generator.scene_generator import generate_scenes
        section = {
            "id": "section_03", "title": "S3",
            "narration": "surface.", "cue_word_indices": [0],
            # Backslash-free visual (A2 contract) that still names the 3D
            # technique — exactly the shape the planner now emits.
            "cues": [{"index": 0, "visual": "Technique: 3d_surface. A ParametricSurface of f(x,y)=§sin(x)§cos(y)."}],
        }
        code, _, _ = generate_scenes(section, cue_durations=[5.0])
        self.assertIn("ThreeDScene", code)
        self.assertNotIn("(Scene):", code)

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_fencing_stripped_from_llm_output(self, mock_paths, mock_chat):
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = "```python\nfrom manimlib import *\n\nclass Section01Scene(Scene):\n    def construct(self):\n        self.wait(5.0)\n```"

        from manimgen.generator.scene_generator import generate_scenes
        code, _, _ = generate_scenes(self._make_section(), cue_durations=[5.0])
        self.assertNotIn("```", code)
        self.assertIn("from manimlib import *", code)


class TestCodeguardPrecheck(unittest.TestCase):
    """precheck_and_autofix() works on code strings, not file paths."""

    def test_fixes_wrong_import(self):
        from manimgen.validator.codeguard import precheck_and_autofix
        code = "from manim import *\n\nclass S(Scene):\n    def construct(self): pass\n"
        fixed = precheck_and_autofix(code)
        self.assertIn("from manimlib import *", fixed)
        self.assertNotIn("from manim import *", fixed)

    def test_fixes_mathtex(self):
        from manimgen.validator.codeguard import precheck_and_autofix
        code = "from manimlib import *\nMathTex(r'x^2')\n"
        fixed = precheck_and_autofix(code)
        self.assertNotIn("MathTex", fixed)
        self.assertIn("Tex(", fixed)

    def test_returns_string_not_dict(self):
        from manimgen.validator.codeguard import precheck_and_autofix
        code = "from manimlib import *\nclass S(Scene):\n    def construct(self): self.wait(1.0)\n"
        result = precheck_and_autofix(code)
        self.assertIsInstance(result, str)

    def test_precheck_file_returns_dict(self):
        from manimgen.validator.codeguard import precheck_and_autofix_file
        path = "/tmp/test_codeguard_scene.py"
        with open(path, "w") as f:
            f.write("from manimlib import *\nclass S(Scene):\n    def construct(self): self.wait(1)\n")
        result = precheck_and_autofix_file(path)
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)


class TestCutter(unittest.TestCase):
    """cue_start_times_from_durations computes cumulative offsets correctly."""

    def test_start_times_cumulative(self):
        from manimgen.renderer.cutter import cue_start_times_from_durations
        durations = [4.2, 6.1, 5.8]
        starts = cue_start_times_from_durations(durations)
        self.assertAlmostEqual(starts[0], 0.0)
        self.assertAlmostEqual(starts[1], 4.2)
        self.assertAlmostEqual(starts[2], 10.3)

    def test_single_cue(self):
        from manimgen.renderer.cutter import cue_start_times_from_durations
        self.assertEqual(cue_start_times_from_durations([10.0]), [0.0])

    def test_empty(self):
        from manimgen.renderer.cutter import cue_start_times_from_durations
        self.assertEqual(cue_start_times_from_durations([]), [])


class TestBuildUserMessage(unittest.TestCase):
    """_build_user_message includes cue durations and visuals."""

    def test_contains_all_cues(self):
        from manimgen.generator.scene_generator import _build_user_message
        section = {
            "id": "section_01",
            "title": "Test",
            "cues": [
                {"index": 0, "visual": "Circle appears"},
                {"index": 1, "visual": "Text appears"},
            ],
        }
        msg = _build_user_message(section, [4.2, 6.1])
        self.assertIn("CUE 0", msg)
        self.assertIn("CUE 1", msg)
        self.assertIn("Circle appears", msg)
        self.assertIn("4.20", msg)
        self.assertIn("Section01Scene", msg)

    def test_total_duration_correct(self):
        from manimgen.generator.scene_generator import _build_user_message
        section = {"id": "section_01", "title": "T", "cues": []}
        msg = _build_user_message(section, [3.0, 4.0, 5.0])
        self.assertIn("12.00", msg)


if __name__ == "__main__":
    unittest.main()
