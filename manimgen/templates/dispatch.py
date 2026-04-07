"""
Template dispatch: maps spec["template"] to the appropriate template class
and calls from_spec() to render a ManimGL .py file.
"""
from manimgen.templates.function_template import FunctionTemplate
from manimgen.templates.limit_template import LimitTemplate
from manimgen.templates.matrix_template import MatrixTemplate
from manimgen.templates.text_template import TextTemplate
from manimgen.templates.code_template import CodeTemplate
from manimgen.templates.graph_theory_template import GraphTheoryTemplate
from manimgen.templates.number_line_template import NumberLineTemplate
from manimgen.templates.complex_plane_template import ComplexPlaneTemplate
from manimgen.templates.probability_template import ProbabilityTemplate
from manimgen.templates.geometry_template import GeometryTemplate
from manimgen.templates.surface_3d_template import Surface3DTemplate
from manimgen.templates.solid_3d_template import Solid3DTemplate
from manimgen.templates.vector_field_3d_template import VectorField3DTemplate
from manimgen.templates.parametric_3d_template import Parametric3DTemplate

TEMPLATE_MAP = {
    "function":        FunctionTemplate,
    "limit":           LimitTemplate,
    "matrix":          MatrixTemplate,
    "text":            TextTemplate,
    "code":            CodeTemplate,
    "graph_theory":    GraphTheoryTemplate,
    "number_line":     NumberLineTemplate,
    "complex_plane":   ComplexPlaneTemplate,
    "probability":     ProbabilityTemplate,
    "geometry":        GeometryTemplate,
    "surface_3d":      Surface3DTemplate,
    "solid_3d":        Solid3DTemplate,
    "vector_field_3d": VectorField3DTemplate,
    "parametric_3d":   Parametric3DTemplate,
}


def render_spec(spec: dict, class_name: str, output_path: str) -> str:
    template_cls = TEMPLATE_MAP[spec["template"]]
    return template_cls.from_spec(spec, class_name, output_path)
