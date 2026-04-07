"""
Spec schema definition and validation for the ManimGen template engine.
Session B imports validate(), KNOWN_TEMPLATES, and SpecValidationError from here.
"""
import re

KNOWN_TEMPLATES: list[str] = [
    "function",
    "limit",
    "matrix",
    "text",
    "code",
    "graph_theory",
    "number_line",
    "complex_plane",
    "probability",
    "geometry",
    "surface_3d",
    "solid_3d",
    "vector_field_3d",
    "parametric_3d",
]


class SpecValidationError(Exception):
    pass


_REQUIRED_TOP_LEVEL = ["mode", "template", "title", "duration_seconds", "beats"]

_VALID_MODES = {"2d", "3d"}

_3D_TEMPLATES = {"surface_3d", "solid_3d", "vector_field_3d", "parametric_3d"}

_BEAT_REQUIRED_FIELDS: dict[str, dict[str, list[str]]] = {
    "function": {
        "axes_appear": ["x_range", "y_range"],
        "curve_appear": ["expr_str", "color", "x_range"],
        "trace_dot": ["start_x", "end_x", "color"],
        "annotation": ["text", "position", "color"],
        "transition": [],
    },
    "limit": {
        "axes_appear": ["x_range", "y_range"],
        "curve_appear": ["expr_str", "color", "hole_x", "hole_y"],
        "guide_lines": ["limit_x", "limit_y"],
        "approach_dot": ["from_side", "start_x", "end_x", "color"],
        "annotation": ["value_label", "limit_label"],
    },
    "matrix": {
        "plane_appear": ["x_range", "y_range"],
        "show_matrix": ["data", "position"],
        "apply_transform": ["data", "duration"],
        "show_vector": ["coords", "color"],
        "annotation": ["text", "color"],
    },
    "text": {
        "title_only": ["title", "duration"],
        "bullets": ["items", "colors", "duration"],
        "highlight": ["index", "color"],
        "dim_others": ["keep_index"],
        "transition": [],
    },
    "code": {
        "reveal_lines": ["lines", "duration"],
        "highlight_line": ["index", "annotation"],
        "dim_others": ["keep_index"],
        "transition": [],
    },
    "graph_theory": {
        "graph_appear": ["nodes", "edges", "node_color", "edge_color"],
        "highlight_node": ["index", "color", "duration"],
        "highlight_edge": ["i", "j", "color", "duration"],
        "highlight_path": ["path", "color", "duration"],
        "traverse_bfs": ["start", "color", "duration"],
        "traverse_dfs": ["start", "color", "duration"],
        "annotation": ["text", "position"],
        "transition": [],
    },
    "number_line": {
        "line_appear": ["x_range", "include_numbers"],
        "mark_point": ["value", "color", "label"],
        "mark_interval": ["start", "end", "color"],
        "jump_arrow": ["from_val", "to_val"],
        "annotation": ["text"],
    },
    "complex_plane": {
        "plane_appear": [],
        "plot_point": ["re", "im", "label", "color"],
        "plot_vector": ["re", "im", "color"],
        "rotate_vector": ["re", "im", "angle_degrees", "duration"],
        "show_multiplication": ["z1", "z2"],
        "annotation": ["text", "position"],
    },
    "probability": {
        "bar_chart": ["categories", "values", "colors"],
        "highlight_bar": ["index", "color"],
        "sample_space": ["regions"],
        "annotation": ["text"],
    },
    "geometry": {
        "shape_appear": ["shape", "params", "color"],
        "label_side": ["shape_index", "side", "label"],
        "show_angle": ["vertex", "from_pt", "to_pt", "label"],
        "brace": ["target_index", "direction", "label"],
        "transform": ["from_index", "to_shape", "to_params"],
        "annotation": ["text"],
    },
    "surface_3d": {
        "axes_appear": ["x_range", "y_range", "z_range"],
        "surface_appear": ["expr_str", "color", "opacity"],
        "rotate_camera": ["delta_theta", "delta_phi", "duration"],
        "trace_curve": ["u_range", "color", "duration"],
        "annotation": ["text"],
    },
    "solid_3d": {
        "solid_appear": ["shape", "params", "color"],
        "rotate": ["axis", "angle_degrees", "duration"],
        "rotate_camera": ["delta_theta", "delta_phi", "duration"],
        "annotation": ["text"],
    },
    "vector_field_3d": {
        "axes_appear": ["x_range", "y_range", "z_range"],
        "vector_field": ["func_str", "color"],
        "flow_particle": ["start", "color", "duration"],
        "rotate_camera": ["delta_theta", "duration"],
    },
    "parametric_3d": {
        "axes_appear": ["x_range", "y_range", "z_range"],
        "curve_appear": ["expr_str", "t_range", "color"],
        "rotate_camera": ["delta_theta", "duration"],
    },
}


# Color fields used in beat types — validated for format correctness.
_BEAT_COLOR_FIELDS: dict[str, dict[str, list[str]]] = {
    "function":      {"curve_appear": ["color"], "trace_dot": ["color"], "annotation": ["color"]},
    "limit":         {"curve_appear": ["color"], "approach_dot": ["color"]},
    "matrix":        {"show_vector": ["color"], "annotation": ["color"]},
    "text":          {"bullets": ["colors"], "highlight": ["color"]},
    "code":          {},
    "graph_theory":  {"graph_appear": ["node_color", "edge_color"], "highlight_node": ["color"],
                      "highlight_edge": ["color"], "highlight_path": ["color"],
                      "traverse_bfs": ["color"], "traverse_dfs": ["color"]},
    "number_line":   {"mark_point": ["color"], "mark_interval": ["color"]},
    "complex_plane": {"plot_point": ["color"], "plot_vector": ["color"], "rotate_vector": ["color"]},
    "probability":   {"bar_chart": ["colors"], "highlight_bar": ["color"]},
    "geometry":      {"shape_appear": ["color"]},
    "surface_3d":    {"surface_appear": ["color"], "trace_curve": ["color"]},
    "solid_3d":      {"solid_appear": ["color"]},
    "vector_field_3d": {"vector_field": ["color"], "flow_particle": ["color"]},
    "parametric_3d": {"curve_appear": ["color"]},
}

# Beat ordering rules: for function template, trace_dot must follow curve_appear.
_BEAT_ORDER_RULES: dict[str, list[tuple[str, str]]] = {
    "function": [("trace_dot", "curve_appear")],  # (dependent, must_precede)
}

_HEX_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
_MANIMGL_KNOWN_UPPER = {
    "WHITE", "BLACK", "GREY", "GRAY",
    "GREY_A", "GREY_B", "GREY_C", "GREY_D", "GREY_E",
    "GRAY_A", "GRAY_B", "GRAY_C", "GRAY_D", "GRAY_E",
    "BLUE", "BLUE_A", "BLUE_B", "BLUE_C", "BLUE_D", "BLUE_E",
    "TEAL", "TEAL_A", "TEAL_B", "TEAL_C", "TEAL_D", "TEAL_E",
    "GREEN", "GREEN_A", "GREEN_B", "GREEN_C", "GREEN_D", "GREEN_E",
    "YELLOW", "YELLOW_A", "YELLOW_B", "YELLOW_C", "YELLOW_D", "YELLOW_E",
    "GOLD", "GOLD_A", "GOLD_B", "GOLD_C", "GOLD_D", "GOLD_E",
    "RED", "RED_A", "RED_B", "RED_C", "RED_D", "RED_E",
    "MAROON", "MAROON_A", "MAROON_B", "MAROON_C", "MAROON_D", "MAROON_E",
    "PURPLE", "PURPLE_A", "PURPLE_B", "PURPLE_C", "PURPLE_D", "PURPLE_E",
    "PINK", "LIGHT_PINK", "ORANGE",
}


def _is_valid_color(value: str) -> bool:
    """Return True if value is a valid ManimGL color (hex or known constant, case-insensitive)."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if _HEX_COLOR_RE.match(s):
        return True
    if s.upper() in _MANIMGL_KNOWN_UPPER:
        return True
    return False


def validate(spec: dict) -> list[str]:
    """
    Validate a visual spec dict.
    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    for field in _REQUIRED_TOP_LEVEL:
        if field not in spec:
            errors.append(f"Missing required top-level field: '{field}'")

    if errors:
        return errors

    mode = spec.get("mode")
    if mode not in _VALID_MODES:
        errors.append(f"Invalid mode '{mode}'. Must be one of: {sorted(_VALID_MODES)}")

    template = spec.get("template")
    if template not in KNOWN_TEMPLATES:
        errors.append(f"Unknown template '{template}'. Known: {KNOWN_TEMPLATES}")

    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        errors.append("Field 'title' must be a non-empty string")

    duration = spec.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append("Field 'duration_seconds' must be a positive number")

    beats = spec.get("beats")
    if not isinstance(beats, list):
        errors.append("Field 'beats' must be a list")
        return errors

    if len(beats) == 0:
        errors.append("Field 'beats' must not be empty")

    if template in _3D_TEMPLATES and mode != "3d":
        errors.append(f"Template '{template}' requires mode '3d'")

    if template in KNOWN_TEMPLATES and template not in _3D_TEMPLATES and mode == "3d":
        errors.append(f"2D template '{template}' should use mode '2d'")

    if template in _BEAT_REQUIRED_FIELDS:
        known_beat_types = _BEAT_REQUIRED_FIELDS[template]
        color_rules = _BEAT_COLOR_FIELDS.get(template, {})
        seen_beat_types: list[str] = []

        for i, beat in enumerate(beats):
            if not isinstance(beat, dict):
                errors.append(f"beats[{i}] must be a dict")
                continue
            beat_type = beat.get("type")
            if not beat_type:
                errors.append(f"beats[{i}] missing 'type' field")
                continue
            if beat_type not in known_beat_types:
                errors.append(
                    f"beats[{i}] has unknown type '{beat_type}' for template '{template}'. "
                    f"Known: {list(known_beat_types.keys())}"
                )
                continue
            required = known_beat_types[beat_type]
            for req_field in required:
                if req_field not in beat:
                    errors.append(f"beats[{i}] (type='{beat_type}') missing required field '{req_field}'")

            # Color value validation
            if beat_type in color_rules:
                for color_field in color_rules[beat_type]:
                    val = beat.get(color_field)
                    if val is None:
                        continue
                    # Handle list of colors (e.g. bullets.colors)
                    if isinstance(val, list):
                        for j, cv in enumerate(val):
                            if not _is_valid_color(cv):
                                errors.append(
                                    f"beats[{i}] (type='{beat_type}') field '{color_field}[{j}]' "
                                    f"has invalid color '{cv}'. Use a ManimGL constant (e.g. YELLOW) or hex #RRGGBB."
                                )
                    else:
                        if not _is_valid_color(val):
                            errors.append(
                                f"beats[{i}] (type='{beat_type}') field '{color_field}' "
                                f"has invalid color '{val}'. Use a ManimGL constant (e.g. YELLOW) or hex #RRGGBB."
                            )

            seen_beat_types.append(beat_type)

        # Beat ordering rules
        if template in _BEAT_ORDER_RULES:
            for dependent, must_precede in _BEAT_ORDER_RULES[template]:
                dep_indices = [i for i, b in enumerate(beats) if isinstance(b, dict) and b.get("type") == dependent]
                prec_indices = [i for i, b in enumerate(beats) if isinstance(b, dict) and b.get("type") == must_precede]
                for dep_i in dep_indices:
                    if not any(p < dep_i for p in prec_indices):
                        errors.append(
                            f"beats[{dep_i}] (type='{dependent}') requires a '{must_precede}' beat to appear before it."
                        )

    return errors
