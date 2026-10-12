"""The one registry of animation techniques.

Every consumer reads technique names from here: the planner's menu check, the
storyboard critic's prompt, the Director's ThreeDScene promotion, and the
example-tag checks. The prompt tables in planner_system.md and
director_system.md are prose for the LLM; tests keep them in sync with this
module, so add a technique here first and the tests name the other places.
"""

from __future__ import annotations

from typing import NamedTuple


class Technique(NamedTuple):
    name: str
    is_3d: bool
    description: str


TECHNIQUES: tuple[Technique, ...] = (
    Technique("stagger_reveal", False, "items appearing one by one"),
    Technique("sweep_highlight", False, "a highlight scanning across a sequence"),
    Technique("array_swap", False, "two elements exchanging positions"),
    Technique("camera_zoom", False, "zoom the frame onto a focal point"),
    Technique("equation_morph", False, "algebra steps via TransformMatchingTex"),
    Technique("color_fill", False, "shaded area under a curve or region"),
    Technique("grid_transform", False, "a linear map applied to a NumberPlane"),
    Technique("tracker_label", False, "a continuously changing value with a label"),
    Technique("brace_annotation", False, "a brace labeling a span or interval"),
    Technique("split_screen", False, "two panels side by side"),
    Technique("fade_reveal", False, "clear clutter, then reveal a key statement"),
    Technique("axes_curve", False, "a standard function plot"),
    Technique("code_reveal", False, "pseudocode appearing line by line"),
    Technique("3d_surface", True, "a surface or parametric curve in 3D"),
    Technique("camera_rotation", True, "a 3D object spinning to show all faces"),
    Technique("camera_flythrough", True, "the camera visiting several viewpoints"),
    Technique("dot_product_3d", True, "two 3D vectors, angle and projection"),
    Technique("cross_section_3d", True, "a cutting plane slicing a 3D surface"),
    Technique("value_tracker_tracer", False, "a dot tracing a curve as it sweeps"),
    Technique("lagged_path", False, "elements arriving along arcs"),
    Technique("apply_matrix", False, "apply_matrix on a coordinate grid"),
)

TECHNIQUE_NAMES: frozenset[str] = frozenset(t.name for t in TECHNIQUES)
THREE_D_TECHNIQUES: frozenset[str] = frozenset(t.name for t in TECHNIQUES if t.is_3d)
