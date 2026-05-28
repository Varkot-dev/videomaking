"""
Tests for 3D scene support in the manimgen pipeline.

Verifies:
1. parametric_surface_scene.py has the techniques tag (3d_surface, camera_rotation).
2. _index_examples() includes '3d_surface' as a key after the new example exists.
3. The Scene→ThreeDScene substitution logic in generate_scenes() works correctly.

No LLM calls, no manimgl calls.
"""

import os
import re
import unittest
from unittest.mock import patch

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")
SCENE_FILE = os.path.join(EXAMPLES_DIR, "parametric_surface_scene.py")


class TestParametricSurfaceExampleTag(unittest.TestCase):

    def _read(self):
        with open(SCENE_FILE) as f:
            return f.read()

    def test_file_exists(self):
        self.assertTrue(os.path.isfile(SCENE_FILE), "parametric_surface_scene.py must exist")

    def test_has_techniques_tag(self):
        src = self._read()
        self.assertIn("techniques:", src, "file must contain a 'techniques:' tag in its docstring")

    def test_tag_includes_3d_surface(self):
        src = self._read()
        m = re.search(r'techniques:\s*(.+)', src, re.IGNORECASE)
        self.assertIsNotNone(m, "techniques tag not found")
        techniques = [t.strip() for t in m.group(1).split(",")]
        self.assertIn("3d_surface", techniques)

    def test_tag_includes_camera_rotation(self):
        src = self._read()
        m = re.search(r'techniques:\s*(.+)', src, re.IGNORECASE)
        self.assertIsNotNone(m, "techniques tag not found")
        techniques = [t.strip() for t in m.group(1).split(",")]
        self.assertIn("camera_rotation", techniques)

    def test_uses_manimlib(self):
        src = self._read()
        self.assertIn("from manimlib import *", src)

    def test_uses_three_d_scene(self):
        src = self._read()
        self.assertIn("ThreeDScene", src)

    def test_uses_parametric_surface(self):
        src = self._read()
        self.assertIn("ParametricSurface(", src)

    def test_uses_three_d_axes(self):
        src = self._read()
        self.assertIn("ThreeDAxes(", src)

    def test_uses_surface_mesh(self):
        src = self._read()
        self.assertIn("SurfaceMesh(", src)

    def test_ends_with_fadeout(self):
        src = self._read()
        self.assertIn("FadeOut", src)


class TestIndexExamplesIncludes3DSurface(unittest.TestCase):
    """_index_examples() must include '3d_surface' once parametric_surface_scene.py exists."""

    def test_3d_surface_in_index(self):
        from manimgen.generator.scene_generator import _index_examples
        index = _index_examples()
        self.assertIn(
            "3d_surface", index,
            "'3d_surface' must appear in _index_examples() after parametric_surface_scene.py was added",
        )

    def test_camera_rotation_in_index(self):
        from manimgen.generator.scene_generator import _index_examples
        index = _index_examples()
        self.assertIn(
            "camera_rotation", index,
            "'camera_rotation' must appear in _index_examples() after parametric_surface_scene.py was added",
        )

    def test_parametric_surface_scene_indexed(self):
        from manimgen.generator.scene_generator import _index_examples
        index = _index_examples()
        all_paths = [p for paths in index.values() for p in paths]
        self.assertTrue(
            any("parametric_surface_scene.py" in p for p in all_paths),
            "parametric_surface_scene.py must be indexed",
        )


class TestThreeDSceneSubstitution(unittest.TestCase):
    """
    Test the Scene→ThreeDScene promotion logic in generate_scenes() in isolation.

    Strategy: mock chat() to return a stub Scene class, then call generate_scenes()
    with a section containing 3d_surface in a cue visual, and assert the saved file
    uses ThreeDScene.
    """

    STUB_CODE = (
        "from manimlib import *\n\n"
        "class TestSection01(Scene):\n"
        "    def construct(self):\n"
        "        self.wait(1.0)\n"
    )

    def _make_section(self, visual: str) -> dict:
        return {
            "id": "section_01",
            "title": "Test Section",
            "narration": "Hello world.",
            "cues": [{"index": 0, "visual": visual}],
        }

    def test_3d_surface_cue_promotes_to_threedscene(self):
        import tempfile
        from manimgen.generator import scene_generator

        section = self._make_section("Technique: 3d_surface. Show a ParametricSurface.")

        with (
            patch("manimgen.generator.scene_generator.chat", return_value=self.STUB_CODE),
            patch("manimgen.generator.scene_generator.precheck_and_autofix", side_effect=lambda x: x),
            patch("manimgen.generator.scene_generator.paths") as mock_paths,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_paths.scenes_dir.return_value = tmpdir
            code, class_name, scene_path = scene_generator.generate_scenes(
                section, cue_durations=[5.0]
            )

        self.assertIn("class TestSection01(ThreeDScene):", code)
        self.assertNotIn("class TestSection01(Scene):", code)

    def test_camera_rotation_cue_promotes_to_threedscene(self):
        import tempfile
        from manimgen.generator import scene_generator

        section = self._make_section("Technique: camera_rotation. Spin the object.")

        with (
            patch("manimgen.generator.scene_generator.chat", return_value=self.STUB_CODE),
            patch("manimgen.generator.scene_generator.precheck_and_autofix", side_effect=lambda x: x),
            patch("manimgen.generator.scene_generator.paths") as mock_paths,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_paths.scenes_dir.return_value = tmpdir
            code, class_name, scene_path = scene_generator.generate_scenes(
                section, cue_durations=[5.0]
            )

        self.assertIn("class TestSection01(ThreeDScene):", code)

    def test_2d_cue_keeps_scene(self):
        import tempfile
        from manimgen.generator import scene_generator

        section = self._make_section("Technique: axes_curve. Plot y=x^2.")

        with (
            patch("manimgen.generator.scene_generator.chat", return_value=self.STUB_CODE),
            patch("manimgen.generator.scene_generator.precheck_and_autofix", side_effect=lambda x: x),
            patch("manimgen.generator.scene_generator.paths") as mock_paths,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_paths.scenes_dir.return_value = tmpdir
            code, class_name, scene_path = scene_generator.generate_scenes(
                section, cue_durations=[5.0]
            )

        self.assertIn("class TestSection01(Scene):", code)
        self.assertNotIn("ThreeDScene", code)

    def test_substitution_writes_file(self):
        import tempfile
        from manimgen.generator import scene_generator

        section = self._make_section("Technique: 3d_surface. Show axes and surface.")

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("manimgen.generator.scene_generator.chat", return_value=self.STUB_CODE),
                patch("manimgen.generator.scene_generator.precheck_and_autofix", side_effect=lambda x: x),
                patch("manimgen.generator.scene_generator.paths") as mock_paths,
            ):
                mock_paths.scenes_dir.return_value = tmpdir
                code, class_name, scene_path = scene_generator.generate_scenes(
                    section, cue_durations=[5.0]
                )

            self.assertTrue(os.path.isfile(scene_path))
            with open(scene_path) as f:
                saved = f.read()
            self.assertIn("ThreeDScene", saved)


class TestGenerateScenesPrecheckGate(unittest.TestCase):
    """generate_scenes must surface a failed file precheck instead of
    silently returning code that is already known to be un-renderable."""

    def _make_section(self) -> dict:
        return {
            "id": "section_01",
            "title": "Test Section",
            "narration": "Hello world.",
            "cues": [{"index": 0, "visual": "axes_curve plot"}],
        }

    def test_precheck_failure_raises(self):
        import tempfile
        from manimgen.generator import scene_generator

        # Syntactically broken scene → validate_scene_code returns errors →
        # precheck_and_autofix_file returns {"ok": False}.
        broken = (
            "from manimlib import *\n\n"
            "class TestSection01(Scene):\n"
            "    def construct(self)\n"  # missing colon → SyntaxError
            "        self.wait(1.0)\n"
        )
        with (
            patch("manimgen.generator.scene_generator.chat", return_value=broken),
            patch(
                "manimgen.generator.scene_generator.precheck_and_autofix",
                side_effect=lambda x: x,
            ),
            patch("manimgen.generator.scene_generator.paths") as mock_paths,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_paths.scenes_dir.return_value = tmpdir
            with self.assertRaisesRegex(ValueError, "failed precheck"):
                scene_generator.generate_scenes(
                    self._make_section(), cue_durations=[5.0]
                )

    def test_valid_scene_does_not_raise(self):
        import tempfile
        from manimgen.generator import scene_generator

        good = (
            "from manimlib import *\n\n"
            "class TestSection01(Scene):\n"
            "    def construct(self):\n"
            "        self.wait(1.0)\n"
        )
        with (
            patch("manimgen.generator.scene_generator.chat", return_value=good),
            patch(
                "manimgen.generator.scene_generator.precheck_and_autofix",
                side_effect=lambda x: x,
            ),
            patch("manimgen.generator.scene_generator.paths") as mock_paths,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            mock_paths.scenes_dir.return_value = tmpdir
            # Must not raise; the file is written inside the tmpdir context.
            code, class_name, scene_path = scene_generator.generate_scenes(
                self._make_section(), cue_durations=[5.0]
            )
            self.assertTrue(os.path.isfile(scene_path))
            self.assertIn("class TestSection01(Scene):", code)


class TestRequests3DWordBoundary(unittest.TestCase):
    """_requests_3d uses whole-token matching and skips negated mentions."""

    def setUp(self):
        from manimgen.generator.scene_generator import _requests_3d

        self._requests_3d = staticmethod(_requests_3d)

    def test_plain_tag_triggers(self):
        self.assertTrue(self._requests_3d("technique: 3d_surface here"))
        self.assertTrue(self._requests_3d("use camera_rotation to spin"))

    def test_negated_tag_does_not_trigger(self):
        self.assertFalse(self._requests_3d("no 3d_surface needed for this 2d plot"))
        self.assertFalse(self._requests_3d("without camera_rotation, keep it flat"))
        self.assertFalse(self._requests_3d("avoid 3d_surface; stay in 2d"))

    def test_mixed_negated_and_affirmed_triggers(self):
        # One cue rules it out, another genuinely requests it → must promote.
        text = "cue a: no 3d_surface. cue b: render a 3d_surface mesh"
        self.assertTrue(self._requests_3d(text))

    def test_substring_of_larger_word_does_not_trigger(self):
        # A bare mention buried in another token must not match.
        self.assertFalse(self._requests_3d("the my3d_surfaceX label"))

    def test_no_tag_does_not_trigger(self):
        self.assertFalse(self._requests_3d("plot y=x^2 on axes"))


if __name__ == "__main__":
    unittest.main()
