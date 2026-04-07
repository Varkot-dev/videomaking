"""
Tests for the ManimGen template engine.
Covers spec_schema validation, from_spec() code generation, and dispatch.
"""
import ast
import os
import tempfile
import pytest

from manimgen.templates.spec_schema import validate, SpecValidationError, KNOWN_TEMPLATES
from manimgen.templates.dispatch import render_spec, TEMPLATE_MAP
from manimgen.validator.codeguard import validate_scene_code


# ---------------------------------------------------------------------------
# Minimal valid specs for each template
# ---------------------------------------------------------------------------

def _make_spec(template: str, mode: str = "2d", beats: list = None) -> dict:
    beats = beats or [{"type": "_noop"}]
    return {
        "mode": mode,
        "template": template,
        "title": "Test Scene",
        "duration_seconds": 10.0,
        "beats": beats,
    }


FUNCTION_SPEC = {
    "mode": "2d",
    "template": "function",
    "title": "Graphing Functions",
    "duration_seconds": 12.0,
    "beats": [
        {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
        {"type": "curve_appear", "expr_str": "x**2", "color": "YELLOW", "x_range": [-2.2, 2.2], "label": r"f(x)=x^2"},
    ],
}

LIMIT_SPEC = {
    "mode": "2d",
    "template": "limit",
    "title": "Limit Demo",
    "duration_seconds": 15.0,
    "beats": [
        {"type": "axes_appear", "x_range": [0, 4, 1], "y_range": [0, 4, 1]},
        {"type": "curve_appear", "expr_str": "x + 1", "color": "BLUE", "hole_x": 1.0, "hole_y": 2.0},
        {"type": "guide_lines", "limit_x": 1.0, "limit_y": 2.0},
        {"type": "annotation", "value_label": "y = 2", "limit_label": r"\lim_{x\to 1} f(x) = 2"},
    ],
}

MATRIX_SPEC = {
    "mode": "2d",
    "template": "matrix",
    "title": "Matrix Transform",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "plane_appear", "x_range": [-4, 4, 1], "y_range": [-3, 3, 1]},
        {"type": "show_matrix", "data": [[1, 2], [0, 1]], "position": "UL"},
        {"type": "apply_transform", "data": [[1, 2], [0, 1]], "duration": 2.0},
    ],
}

TEXT_SPEC = {
    "mode": "2d",
    "template": "text",
    "title": "Binary Search",
    "duration_seconds": 8.0,
    "beats": [
        {"type": "title_only", "title": "Binary Search", "duration": 2.0},
        {"type": "bullets", "items": ["Sorted array", "Divide and conquer"], "colors": ["WHITE", "YELLOW"], "duration": 2.5},
        {"type": "highlight", "index": 1, "color": "YELLOW"},
    ],
}

CODE_SPEC = {
    "mode": "2d",
    "template": "code",
    "title": "Algorithm",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "reveal_lines", "lines": [{"text": "def foo():", "color": "WHITE"}, {"text": "    return 42", "color": "GREEN"}], "duration": 2.0},
        {"type": "highlight_line", "index": 1, "annotation": "returns value"},
        {"type": "dim_others", "keep_index": 1},
    ],
}

GRAPH_THEORY_SPEC = {
    "mode": "2d",
    "template": "graph_theory",
    "title": "Graph Theory",
    "duration_seconds": 12.0,
    "beats": [
        {
            "type": "graph_appear",
            "nodes": [[0, 0], [1, 0], [0.5, 0.8]],
            "edges": [[0, 1], [1, 2], [0, 2]],
            "node_color": "WHITE",
            "edge_color": "GREY_A",
        },
        {"type": "highlight_node", "index": 0, "color": "YELLOW", "duration": 1.0},
        {"type": "highlight_path", "path": [0, 1, 2], "color": "GREEN", "duration": 1.5},
    ],
}

NUMBER_LINE_SPEC = {
    "mode": "2d",
    "template": "number_line",
    "title": "Number Line",
    "duration_seconds": 8.0,
    "beats": [
        {"type": "line_appear", "x_range": [-5, 5, 1], "include_numbers": True},
        {"type": "mark_point", "value": 2.0, "color": "YELLOW", "label": "2"},
        {"type": "annotation", "text": "This is 2"},
    ],
}

COMPLEX_PLANE_SPEC = {
    "mode": "2d",
    "template": "complex_plane",
    "title": "Complex Numbers",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "plane_appear"},
        {"type": "plot_point", "re": 1.0, "im": 1.0, "label": "1+i", "color": "YELLOW"},
        {"type": "annotation", "text": "Complex plane", "position": "right"},
    ],
}

PROBABILITY_SPEC = {
    "mode": "2d",
    "template": "probability",
    "title": "Probability",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "bar_chart", "categories": ["A", "B", "C"], "values": [0.5, 0.3, 0.2], "colors": ["BLUE", "RED", "GREEN"]},
        {"type": "annotation", "text": "Distribution"},
    ],
}

GEOMETRY_SPEC = {
    "mode": "2d",
    "template": "geometry",
    "title": "Geometry",
    "duration_seconds": 8.0,
    "beats": [
        {"type": "shape_appear", "shape": "circle", "params": {"radius": 1.5}, "color": "BLUE"},
        {"type": "annotation", "text": "A circle"},
    ],
}

SURFACE_3D_SPEC = {
    "mode": "3d",
    "template": "surface_3d",
    "title": "3D Surface",
    "duration_seconds": 12.0,
    "beats": [
        {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-3, 3, 1], "z_range": [-2, 2, 1]},
        {"type": "surface_appear", "expr_str": "np.sin(x) * np.cos(y)", "color": "BLUE", "opacity": 0.8},
        {"type": "rotate_camera", "delta_theta": 30, "delta_phi": 0, "duration": 2.0},
    ],
}

SOLID_3D_SPEC = {
    "mode": "3d",
    "template": "solid_3d",
    "title": "3D Solid",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "solid_appear", "shape": "sphere", "params": {"radius": 1.5}, "color": "BLUE"},
        {"type": "rotate", "axis": "y", "angle_degrees": 180, "duration": 2.0},
        {"type": "rotate_camera", "delta_theta": 45, "delta_phi": 0, "duration": 2.0},
    ],
}

VECTOR_FIELD_3D_SPEC = {
    "mode": "3d",
    "template": "vector_field_3d",
    "title": "Vector Field",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-3, 3, 1], "z_range": [-3, 3, 1]},
        {"type": "vector_field", "func_str": "[-y, x, 0]", "color": "BLUE"},
        {"type": "rotate_camera", "delta_theta": 45, "duration": 2.0},
    ],
}

PARAMETRIC_3D_SPEC = {
    "mode": "3d",
    "template": "parametric_3d",
    "title": "Parametric Curve",
    "duration_seconds": 10.0,
    "beats": [
        {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-3, 3, 1], "z_range": [-3, 3, 1]},
        {"type": "curve_appear", "expr_str": "[np.cos(t), np.sin(t), t/3]", "t_range": [0, 6.28], "color": "YELLOW"},
        {"type": "rotate_camera", "delta_theta": 30, "duration": 2.0},
    ],
}

ALL_SPECS = {
    "function": FUNCTION_SPEC,
    "limit": LIMIT_SPEC,
    "matrix": MATRIX_SPEC,
    "text": TEXT_SPEC,
    "code": CODE_SPEC,
    "graph_theory": GRAPH_THEORY_SPEC,
    "number_line": NUMBER_LINE_SPEC,
    "complex_plane": COMPLEX_PLANE_SPEC,
    "probability": PROBABILITY_SPEC,
    "geometry": GEOMETRY_SPEC,
    "surface_3d": SURFACE_3D_SPEC,
    "solid_3d": SOLID_3D_SPEC,
    "vector_field_3d": VECTOR_FIELD_3D_SPEC,
    "parametric_3d": PARAMETRIC_3D_SPEC,
}


# ---------------------------------------------------------------------------
# spec_schema tests
# ---------------------------------------------------------------------------

class TestSpecSchema:

    def test_valid_function_spec(self):
        errors = validate(FUNCTION_SPEC)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_valid_limit_spec(self):
        assert validate(LIMIT_SPEC) == []

    def test_valid_surface_3d_spec(self):
        assert validate(SURFACE_3D_SPEC) == []

    def test_missing_required_fields(self):
        for field in ["mode", "template", "title", "duration_seconds", "beats"]:
            bad = {k: v for k, v in FUNCTION_SPEC.items() if k != field}
            errors = validate(bad)
            assert any(field in e for e in errors), f"Expected error for missing '{field}', got: {errors}"

    def test_invalid_mode(self):
        bad = {**FUNCTION_SPEC, "mode": "4d"}
        errors = validate(bad)
        assert any("mode" in e for e in errors)

    def test_unknown_template(self):
        bad = {**FUNCTION_SPEC, "template": "nonexistent"}
        errors = validate(bad)
        assert any("nonexistent" in e for e in errors)

    def test_empty_beats(self):
        bad = {**FUNCTION_SPEC, "beats": []}
        errors = validate(bad)
        assert any("beats" in e for e in errors)

    def test_beats_missing_type(self):
        bad = {**FUNCTION_SPEC, "beats": [{"x_range": [-3, 3]}]}
        errors = validate(bad)
        assert any("type" in e for e in errors)

    def test_beat_missing_required_field(self):
        bad = {**FUNCTION_SPEC, "beats": [{"type": "axes_appear"}]}
        errors = validate(bad)
        assert any("x_range" in e for e in errors)

    def test_3d_template_requires_3d_mode(self):
        bad = {**SURFACE_3D_SPEC, "mode": "2d"}
        errors = validate(bad)
        assert any("3d" in e for e in errors)

    def test_2d_template_with_3d_mode(self):
        bad = {**FUNCTION_SPEC, "mode": "3d"}
        errors = validate(bad)
        assert any("2d" in e for e in errors)

    def test_known_templates_list(self):
        assert len(KNOWN_TEMPLATES) == 14
        assert "function" in KNOWN_TEMPLATES
        assert "surface_3d" in KNOWN_TEMPLATES

    def test_valid_returns_empty_list(self):
        for name, spec in ALL_SPECS.items():
            errors = validate(spec)
            assert errors == [], f"Template '{name}' validation failed: {errors}"


# ---------------------------------------------------------------------------
# from_spec() code generation tests
# ---------------------------------------------------------------------------

class TestFromSpec:

    def _render(self, spec: dict) -> str:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            code = render_spec(spec, "TestScene", path)
            with open(path) as f:
                on_disk = f.read()
            assert code == on_disk, "from_spec return value must match written file"
            return code
        finally:
            os.unlink(path)

    def test_all_templates_parse(self):
        for name, spec in ALL_SPECS.items():
            code = self._render(spec)
            try:
                ast.parse(code)
            except SyntaxError as e:
                pytest.fail(f"Template '{name}' produced invalid Python: {e}\n\nCode:\n{code}")

    def test_all_templates_pass_codeguard(self):
        for name, spec in ALL_SPECS.items():
            code = self._render(spec)
            errors = validate_scene_code(code)
            assert errors == [], f"Template '{name}' failed codeguard: {errors}\n\nCode:\n{code}"

    def test_output_starts_with_manimlib_import(self):
        code = self._render(FUNCTION_SPEC)
        assert code.startswith("from manimlib import *"), "Must start with manimlib import"

    def test_exactly_one_class(self):
        code = self._render(FUNCTION_SPEC)
        tree = ast.parse(code)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert len(classes) == 1
        assert classes[0].name == "TestScene"

    def test_3d_templates_use_threedscene(self):
        for name in ["surface_3d", "solid_3d", "vector_field_3d", "parametric_3d"]:
            code = self._render(ALL_SPECS[name])
            assert "ThreeDScene" in code, f"Template '{name}' must use ThreeDScene"

    def test_2d_templates_do_not_use_threedscene(self):
        for name in ["function", "limit", "matrix", "text", "code", "graph_theory"]:
            code = self._render(ALL_SPECS[name])
            assert "ThreeDScene" not in code, f"Template '{name}' must not use ThreeDScene"

    def test_cleanup_fadeout_present(self):
        for name, spec in ALL_SPECS.items():
            code = self._render(spec)
            assert "FadeOut" in code, f"Template '{name}' missing FadeOut cleanup"

    def test_title_in_generated_code(self):
        code = self._render(FUNCTION_SPEC)
        assert "Graphing Functions" in code

    def test_file_written_to_output_path(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            render_spec(FUNCTION_SPEC, "MyScene", path)
            assert os.path.exists(path)
            with open(path) as f:
                content = f.read()
            assert "class MyScene" in content
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# dispatch tests
# ---------------------------------------------------------------------------

class TestDispatch:

    def test_unknown_template_raises_key_error(self):
        bad_spec = {**FUNCTION_SPEC, "template": "does_not_exist"}
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(KeyError):
                render_spec(bad_spec, "TestScene", path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_template_map_has_all_known_templates(self):
        for name in KNOWN_TEMPLATES:
            assert name in TEMPLATE_MAP, f"'{name}' missing from TEMPLATE_MAP"

    def test_render_spec_returns_string(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            result = render_spec(FUNCTION_SPEC, "TestScene", path)
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Bug regression tests
# ---------------------------------------------------------------------------

class TestBugFixes:

    def _render(self, spec: dict) -> str:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            return render_spec(spec, "TestScene", path)
        finally:
            os.unlink(path)

    # Task 1: annotation positioning must not call .to_edge() on axes
    def test_annotation_does_not_mutate_axes(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "annotation", "text": "Note", "position": "right", "color": "WHITE"},
        ]}
        code = self._render(spec)
        assert "axes.to_edge(" not in code, "annotation must not call axes.to_edge()"
        assert "move_to(" in code or "next_to(" in code

    # Task 2 + 14: multiple curve_appear beats get unique names; trace_dot uses last curve
    def test_multiple_curves_get_unique_variable_names(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "YELLOW", "x_range": [-2, 2]},
            {"type": "curve_appear", "expr_str": "x**3", "color": "RED", "x_range": [-2, 2]},
        ]}
        code = self._render(spec)
        assert "curve_0 = " in code
        assert "curve_1 = " in code

    def test_trace_dot_uses_last_curve_variable(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "YELLOW", "x_range": [-2, 2]},
            {"type": "curve_appear", "expr_str": "x**3", "color": "RED", "x_range": [-2, 2]},
            {"type": "trace_dot", "start_x": -2, "end_x": 2, "color": "GREEN", "duration": 2},
        ]}
        code = self._render(spec)
        assert "curve_1" in code  # trace_dot should reference the second curve

    def test_trace_dot_before_curve_emits_comment_not_crash(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "trace_dot", "start_x": -2, "end_x": 2, "color": "RED", "duration": 2},
        ]}
        code = self._render(spec)
        ast.parse(code)  # must parse; no NameError-inducing reference
        assert "# trace_dot skipped" in code

    # Task 3: transition with no title must not reference undefined 'title' variable
    def test_transition_no_title_no_title_reference(self):
        spec = {"mode": "2d", "template": "function", "title": "", "duration_seconds": 10, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "transition"},
        ]}
        code = self._render(spec)
        ast.parse(code)
        # When no title, transition must not reference the 'title' variable
        assert "if m is not title" not in code

    # Task 4: double-FadeOut — scenes ending with transition must not get a second FadeOut
    def test_no_double_fadeout_after_transition(self):
        spec = {"mode": "2d", "template": "text", "title": "T", "duration_seconds": 8, "beats": [
            {"type": "bullets", "items": ["A", "B"], "colors": ["WHITE", "YELLOW"], "duration": 2},
            {"type": "transition"},
        ]}
        code = self._render(spec)
        # Count FadeOut occurrences in the last 5 lines
        tail_lines = [l for l in code.split("\n") if "FadeOut" in l]
        assert len(tail_lines) == 1, f"Expected 1 FadeOut line, got {len(tail_lines)}: {tail_lines}"

    # Task 5: limit approach_dot must not use .t_max / .t_min
    def test_limit_approach_dot_no_t_max_t_min(self):
        spec = {**LIMIT_SPEC, "beats": LIMIT_SPEC["beats"] + [
            {"type": "approach_dot", "from_side": "both", "start_x": 0.1, "end_x": 2.8, "color": "YELLOW"},
        ]}
        code = self._render(spec)
        assert ".t_max" not in code, "t_max does not exist on ManimGL ParametricCurve"
        assert ".t_min" not in code, "t_min does not exist on ManimGL ParametricCurve"

    # Task 6: BarChart must use ManimGL API (max_value, height, width — no y_range/y_length)
    def test_bar_chart_uses_manimgl_api(self):
        code = self._render(PROBABILITY_SPEC)
        assert "y_range=" not in code, "y_range is ManimCommunity API"
        assert "y_length=" not in code, "y_length is ManimCommunity API"
        assert "x_length=" not in code, "x_length is ManimCommunity API"
        assert "max_value=" in code

    # Task 7: VectorField must use coordinate_system, not delta_x/delta_y
    def test_vector_field_uses_coordinate_system(self):
        code = self._render(VECTOR_FIELD_3D_SPEC)
        assert "delta_x=" not in code
        assert "coordinate_system=" in code

    # Task 8: ParametricCurve t_range must have 3 elements
    def test_parametric_3d_t_range_has_step(self):
        code = self._render(PARAMETRIC_3D_SPEC)
        assert "t_range=(0, 6.28, 0.05)" in code or "t_range=(0," in code
        # Ensure it's not 2-element
        import re
        bad = re.search(r"t_range=\[\s*[\d.]+\s*,\s*[\d.]+\s*\]", code)
        assert bad is None, f"Found 2-element t_range list: {bad.group()}"

    # Task 9: 3D objects must not use .set_style()
    def test_solid_3d_no_set_style(self):
        code = self._render(SOLID_3D_SPEC)
        assert ".set_style(" not in code

    def test_surface_3d_no_set_style(self):
        code = self._render(SURFACE_3D_SPEC)
        assert ".set_style(" not in code

    # Task 10: 3D camera must use dtheta/dphi not theta/phi
    def test_3d_camera_uses_dtheta(self):
        for name in ["surface_3d", "solid_3d", "vector_field_3d", "parametric_3d"]:
            code = self._render(ALL_SPECS[name])
            assert "dtheta=" in code, f"Template '{name}' must use dtheta= not theta="
            assert "increment_euler_angles(theta=" not in code

    # Task 11: geometry show_angle must not use Tex font_size=
    def test_geometry_show_angle_no_tex_font_size(self):
        spec = {**GEOMETRY_SPEC, "beats": [
            {"type": "shape_appear", "shape": "circle", "params": {"radius": 1.5}, "color": "BLUE"},
            {"type": "show_angle", "vertex": [0, 0], "from_pt": [1, 0], "to_pt": [0, 1], "label": r"\theta"},
        ]}
        code = self._render(spec)
        import re
        bad = re.search(r"Tex\([^)]*font_size\s*=", code)
        assert bad is None, f"Tex() with font_size= found: {bad.group()}"

    # Task 12: multiple shape_appear beats get unique variable names
    def test_geometry_multiple_shapes_unique_vars(self):
        spec = {**GEOMETRY_SPEC, "beats": [
            {"type": "shape_appear", "shape": "circle", "params": {"radius": 1.5}, "color": "BLUE"},
            {"type": "shape_appear", "shape": "square", "params": {"side_length": 2.0}, "color": "RED"},
        ]}
        code = self._render(spec)
        assert "_geo_s0 = " in code
        assert "_geo_s1 = " in code

    # Task 13: graph_theory labels must not use BLACK color
    def test_graph_theory_labels_not_black(self):
        code = self._render(GRAPH_THEORY_SPEC)
        # Labels should be WHITE, not BLACK (invisible on dark background)
        assert "color=BLACK" not in code

    # Task 15: lowercase colors get normalized
    def test_lowercase_color_normalized(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "yellow", "x_range": [-2, 2]},
        ]}
        code = self._render(spec)
        assert "color=yellow" not in code, "lowercase color must be normalized"
        assert "color=YELLOW" in code

    def test_hex_color_gets_quoted(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "#FF4500", "x_range": [-2, 2]},
        ]}
        code = self._render(spec)
        assert 'color="#FF4500"' in code

    # Task 16: code template escape logic
    def test_code_template_backslash_escaping(self):
        spec = {**CODE_SPEC, "beats": [
            {"type": "reveal_lines", "lines": [
                {"text": 'path = "C:\\Users\\foo"', "color": "WHITE"},
            ], "duration": 2},
        ]}
        code = self._render(spec)
        ast.parse(code)  # must parse

    # Task 17: spec_schema color validation
    def test_schema_rejects_invalid_color(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "notacolor", "x_range": [-2, 2]},
        ]}
        errors = validate(spec)
        assert any("color" in e for e in errors), f"Expected color error, got: {errors}"

    def test_schema_accepts_hex_color(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "#FF4500", "x_range": [-2, 2]},
        ]}
        errors = validate(spec)
        assert errors == []

    def test_schema_accepts_lowercase_known_color(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "curve_appear", "expr_str": "x**2", "color": "yellow", "x_range": [-2, 2]},
        ]}
        errors = validate(spec)
        # lowercase known color is valid (gets normalized at codegen time)
        assert errors == []

    def test_schema_ordering_trace_dot_requires_curve_appear(self):
        spec = {**FUNCTION_SPEC, "beats": [
            {"type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1]},
            {"type": "trace_dot", "start_x": -2, "end_x": 2, "color": "RED", "duration": 2},
        ]}
        errors = validate(spec)
        assert any("curve_appear" in e for e in errors)

    # Task 18: vector_field flow_particle must not use hardcoded circle
    def test_vector_field_flow_particle_no_hardcoded_circle(self):
        spec = {**VECTOR_FIELD_3D_SPEC, "beats": VECTOR_FIELD_3D_SPEC["beats"] + [
            {"type": "flow_particle", "start": [1, 0, 0], "color": "YELLOW", "duration": 3.0},
        ]}
        code = self._render(spec)
        # Should not be the exact hardcoded circle formula
        assert "np.cos(t), np.sin(t), 0" not in code or "_fp_euler" in code
