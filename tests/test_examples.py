"""
Tests for new example scenes in manimgen/examples/.

These tests verify:
1. Each file is syntactically valid Python (compile check).
2. Each file uses `from manimlib import *` (not ManimCommunity).
3. Each file defines exactly one Scene subclass.
4. Banned ManimCommunity APIs are absent.
5. Each file demonstrates the technique it claims to (API presence check).
"""

import ast
import os
import py_compile
import tempfile
import unittest

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

NEW_EXAMPLES = [
    "camera_zoom_scene.py",
    "equation_morph_scene.py",
    "color_fill_scene.py",
    "number_plane_transform_scene.py",
    "stagger_build_scene.py",
]

BANNED_APIS = [
    "from manim import",
    "MathTex(",
    "Create(",
    "self.camera.frame",
    "Circumscribe(",
    "x_length=",
    "y_length=",
]


def _read(filename):
    path = os.path.join(EXAMPLES_DIR, filename)
    with open(path) as f:
        return f.read()


class TestExampleSyntax(unittest.TestCase):

    def _check_compiles(self, filename):
        path = os.path.join(EXAMPLES_DIR, filename)
        # py_compile raises SyntaxError on bad code
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as tmp:
            py_compile.compile(path, cfile=tmp.name, doraise=True)

    def test_camera_zoom_compiles(self):
        self._check_compiles("camera_zoom_scene.py")

    def test_equation_morph_compiles(self):
        self._check_compiles("equation_morph_scene.py")

    def test_color_fill_compiles(self):
        self._check_compiles("color_fill_scene.py")

    def test_number_plane_transform_compiles(self):
        self._check_compiles("number_plane_transform_scene.py")

    def test_stagger_build_compiles(self):
        self._check_compiles("stagger_build_scene.py")


class TestExampleImports(unittest.TestCase):

    def _check_uses_manimlib(self, filename):
        src = _read(filename)
        self.assertIn(
            "from manimlib import *", src,
            f"{filename}: must use 'from manimlib import *', not ManimCommunity",
        )

    def test_camera_zoom_uses_manimlib(self):
        self._check_uses_manimlib("camera_zoom_scene.py")

    def test_equation_morph_uses_manimlib(self):
        self._check_uses_manimlib("equation_morph_scene.py")

    def test_color_fill_uses_manimlib(self):
        self._check_uses_manimlib("color_fill_scene.py")

    def test_number_plane_transform_uses_manimlib(self):
        self._check_uses_manimlib("number_plane_transform_scene.py")

    def test_stagger_build_uses_manimlib(self):
        self._check_uses_manimlib("stagger_build_scene.py")


class TestExampleNoBannedAPIs(unittest.TestCase):

    def _check_no_banned(self, filename):
        src = _read(filename)
        for banned in BANNED_APIS:
            self.assertNotIn(
                banned, src,
                f"{filename}: contains banned ManimCommunity API '{banned}'",
            )

    def test_camera_zoom_no_banned(self):
        self._check_no_banned("camera_zoom_scene.py")

    def test_equation_morph_no_banned(self):
        self._check_no_banned("equation_morph_scene.py")

    def test_color_fill_no_banned(self):
        self._check_no_banned("color_fill_scene.py")

    def test_number_plane_transform_no_banned(self):
        self._check_no_banned("number_plane_transform_scene.py")

    def test_stagger_build_no_banned(self):
        self._check_no_banned("stagger_build_scene.py")


class TestExampleTechniquePresence(unittest.TestCase):
    """Each example must contain the API calls that define its technique."""

    def test_camera_zoom_uses_frame_animate(self):
        src = _read("camera_zoom_scene.py")
        self.assertIn("self.frame.animate", src)
        self.assertIn(".scale(", src)

    def test_camera_zoom_uses_flasharound(self):
        src = _read("camera_zoom_scene.py")
        self.assertIn("FlashAround(", src)

    def test_equation_morph_uses_transform_matching_tex(self):
        src = _read("equation_morph_scene.py")
        self.assertIn("TransformMatchingTex(", src)

    def test_equation_morph_uses_lagged_start(self):
        src = _read("equation_morph_scene.py")
        self.assertIn("LaggedStart(", src)

    def test_color_fill_uses_get_area(self):
        src = _read("color_fill_scene.py")
        self.assertIn("get_area(", src)

    def test_color_fill_uses_brace(self):
        src = _read("color_fill_scene.py")
        self.assertIn("Brace(", src)

    def test_number_plane_transform_uses_number_plane(self):
        src = _read("number_plane_transform_scene.py")
        self.assertIn("NumberPlane(", src)

    def test_number_plane_transform_uses_apply_matrix(self):
        src = _read("number_plane_transform_scene.py")
        self.assertIn("apply_matrix(", src)

    def test_stagger_build_uses_lagged_start(self):
        src = _read("stagger_build_scene.py")
        self.assertIn("LaggedStart(", src)

    def test_stagger_build_uses_surrounding_rectangle(self):
        src = _read("stagger_build_scene.py")
        self.assertIn("SurroundingRectangle(", src)


class TestExampleSceneClass(unittest.TestCase):
    """Each example must define exactly one Scene subclass."""

    def _count_scene_classes(self, filename):
        src = _read(filename)
        tree = ast.parse(src)
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = ""
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    if base_name == "Scene":
                        count += 1
        return count

    def test_camera_zoom_has_one_scene(self):
        self.assertEqual(self._count_scene_classes("camera_zoom_scene.py"), 1)

    def test_equation_morph_has_one_scene(self):
        self.assertEqual(self._count_scene_classes("equation_morph_scene.py"), 1)

    def test_color_fill_has_one_scene(self):
        self.assertEqual(self._count_scene_classes("color_fill_scene.py"), 1)

    def test_number_plane_transform_has_one_scene(self):
        self.assertEqual(self._count_scene_classes("number_plane_transform_scene.py"), 1)

    def test_stagger_build_has_one_scene(self):
        self.assertEqual(self._count_scene_classes("stagger_build_scene.py"), 1)
