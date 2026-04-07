"""
MatrixTemplate — generates ManimGL scenes for matrix transformations.
Beat types: plane_appear, show_matrix, apply_transform, show_vector, annotation.
"""
from manimgen.templates.base import TemplateScene


class MatrixTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "plane_appear":
            return self._plane_appear(beat)
        if t == "show_matrix":
            return self._show_matrix(beat)
        if t == "apply_transform":
            return self._apply_transform(beat)
        if t == "show_vector":
            return self._show_vector(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _plane_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-4, 4, 1])
        y_range = beat.get("y_range", [-3, 3, 1])
        return [
            "plane = NumberPlane(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            '    background_line_style={"stroke_color": BLUE_E, "stroke_opacity": 0.5},',
            ")",
            "self.play(ShowCreation(plane), run_time=1.5)",
            "self.wait(0.5)",
        ]

    def _show_matrix(self, beat: dict) -> list[str]:
        data = beat.get("data", [[1, 0], [0, 1]])
        position = beat.get("position", "UL")
        corner_map = {"UL": "UL", "UR": "UR"}
        corner = corner_map.get(position, "UL")
        return [
            f"_matrix_data = {data}",
            "_matrix_mob = IntegerMatrix(_matrix_data)",
            "_matrix_mob.scale(0.8)",
            f"_matrix_mob.to_corner({corner}, buff=0.8)",
            "_matrix_mob.shift(DOWN * 0.5)",
            '_matrix_label = Text("M = ", font_size=32)',
            "_matrix_label.next_to(_matrix_mob, LEFT, buff=0.1)",
            "self.play(Write(_matrix_label), Write(_matrix_mob))",
            "self.wait(1.0)",
        ]

    def _apply_transform(self, beat: dict) -> list[str]:
        data = beat.get("data", [[1, 0], [0, 1]])
        duration = beat.get("duration", 2.0)
        return [
            "self.play(",
            f"    plane.animate.apply_matrix({data}),",
            f"    run_time={duration},",
            "    rate_func=smooth,",
            ")",
            "self.wait(1.5)",
        ]

    def _show_vector(self, beat: dict) -> list[str]:
        coords = beat.get("coords", [1, 1])
        color = beat.get("color", "RED")
        return [
            "_origin = plane.get_origin()",
            f"_v_end = plane.coords_to_point({coords[0]}, {coords[1]})",
            f"_arrow = Arrow(_origin, _v_end, color={color}, buff=0)",
            "self.play(GrowArrow(_arrow))",
            "self.wait(1.0)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        color = beat.get("color", "WHITE")
        escaped = text.replace('"', '\\"')
        return [
            f'_ann = Text("{escaped}", font_size=28, color={color})',
            "_ann.to_edge(DOWN, buff=0.5)",
            "self.play(Write(_ann))",
            "self.wait(1.0)",
        ]
