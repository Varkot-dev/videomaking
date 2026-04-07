"""
GeometryTemplate — generates ManimGL scenes for geometry visualizations.
Beat types: shape_appear, label_side, show_angle, brace, transform, annotation.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class GeometryTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "shape_appear":
            return self._shape_appear(beat)
        if t == "label_side":
            return self._label_side(beat)
        if t == "show_angle":
            return self._show_angle(beat)
        if t == "brace":
            return self._brace(beat)
        if t == "transform":
            return self._transform(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def __init__(self, spec: dict):
        super().__init__(spec)
        self._shape_count = 0  # unique var name per shape_appear beat

    def scene_setup(self) -> list[str]:
        return ["_geo_shapes = []", ""]

    def _shape_appear(self, beat: dict) -> list[str]:
        shape = beat.get("shape", "circle")
        params = beat.get("params", {})
        color = normalize_color(beat.get("color", "BLUE"))
        idx = f"_geo_s{self._shape_count}"
        self._shape_count += 1

        if shape == "circle":
            radius = params.get("radius", 1.5)
            creator = f"Circle(radius={radius}, color={color})"
        elif shape == "square":
            side = params.get("side_length", 2.0)
            creator = f"Square(side_length={side}, color={color})"
        elif shape == "triangle":
            creator = f"Triangle(color={color}).scale({params.get('scale', 1.5)})"
        elif shape == "polygon":
            n = params.get("n", 6)
            creator = f"RegularPolygon(n={n}, color={color})"
        else:
            creator = f"Circle(radius=1.5, color={color})"

        return [
            f"{idx} = {creator}",
            f"{idx}.move_to(ORIGIN)",
            f"_geo_shapes.append({idx})",
            f"self.play(ShowCreation({idx}))",
            "self.wait(0.5)",
        ]

    def _label_side(self, beat: dict) -> list[str]:
        shape_index = beat.get("shape_index", 0)
        side = beat.get("side", "bottom")
        label = beat.get("label", "a")
        escaped = label.replace('"', '\\"')
        dir_map = {
            "bottom": "DOWN", "top": "UP",
            "left": "LEFT", "right": "RIGHT",
        }
        direction = dir_map.get(side, "DOWN")
        return [
            f'_gl_label = Text("{escaped}", font_size=28, color=WHITE)',
            f"_gl_label.next_to(_geo_shapes[{shape_index}], {direction}, buff=0.2)",
            "self.play(Write(_gl_label))",
            "self.wait(0.5)",
        ]

    def _show_angle(self, beat: dict) -> list[str]:
        vertex = beat.get("vertex", [0, 0])
        from_pt = beat.get("from_pt", [1, 0])
        to_pt = beat.get("to_pt", [0, 1])
        label = beat.get("label", "θ")
        escaped = label.replace('"', '\\"')
        return [
            f"_ga_vertex = np.array([{vertex[0]}, {vertex[1]}, 0])",
            f"_ga_from = np.array([{from_pt[0]}, {from_pt[1]}, 0])",
            f"_ga_to = np.array([{to_pt[0]}, {to_pt[1]}, 0])",
            "_ga_arc = Arc(radius=0.4, start_angle=np.arctan2(_ga_from[1]-_ga_vertex[1], _ga_from[0]-_ga_vertex[0]),",
            "             angle=np.arctan2(_ga_to[1]-_ga_vertex[1], _ga_to[0]-_ga_vertex[0]) - np.arctan2(_ga_from[1]-_ga_vertex[1], _ga_from[0]-_ga_vertex[0]))",
            "_ga_arc.shift(_ga_vertex)",
            f'_ga_label = Tex(r"{escaped}").scale(0.9)',
            "_ga_label.move_to(_ga_vertex + 0.6 * (_ga_arc.get_center() - _ga_vertex))",
            "self.play(ShowCreation(_ga_arc), Write(_ga_label))",
            "self.wait(0.5)",
        ]

    def _brace(self, beat: dict) -> list[str]:
        target_index = beat.get("target_index", 0)
        direction = beat.get("direction", "down")
        label = beat.get("label", "")
        dir_map = {"down": "DOWN", "up": "UP", "left": "LEFT", "right": "RIGHT"}
        d = dir_map.get(direction, "DOWN")
        escaped = label.replace('"', '\\"')
        return [
            f"_br = Brace(_geo_shapes[{target_index}], {d})",
            f'_br_label = _br.get_text("{escaped}")',
            "self.play(GrowFromCenter(_br), Write(_br_label))",
            "self.wait(0.8)",
        ]

    def _transform(self, beat: dict) -> list[str]:
        from_index = beat.get("from_index", 0)
        to_shape = beat.get("to_shape", "circle")
        to_params = beat.get("to_params", {})
        color = to_params.get("color", "RED")

        if to_shape == "circle":
            radius = to_params.get("radius", 1.5)
            creator = f"Circle(radius={radius}, color={color})"
        elif to_shape == "square":
            side = to_params.get("side_length", 2.0)
            creator = f"Square(side_length={side}, color={color})"
        elif to_shape == "triangle":
            creator = f"Triangle(color={color}).scale({to_params.get('scale', 1.5)})"
        else:
            creator = f"Circle(radius=1.5, color={color})"

        return [
            f"_tr_target = {creator}",
            f"_tr_target.move_to(_geo_shapes[{from_index}].get_center())",
            f"self.play(ReplacementTransform(_geo_shapes[{from_index}], _tr_target))",
            f"_geo_shapes[{from_index}] = _tr_target",
            "self.wait(0.5)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        escaped = text.replace('"', '\\"')
        return [
            f'_geo_ann = Text("{escaped}", font_size=28, color=WHITE)',
            "_geo_ann.to_edge(DOWN, buff=0.5)",
            "self.play(Write(_geo_ann))",
            "self.wait(1.0)",
        ]
