"""
ComplexPlaneTemplate — generates ManimGL scenes for complex number visualization.
Beat types: plane_appear, plot_point, plot_vector, rotate_vector,
            show_multiplication, annotation.
"""
from manimgen.templates.base import TemplateScene


class ComplexPlaneTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "plane_appear":
            return self._plane_appear(beat)
        if t == "plot_point":
            return self._plot_point(beat)
        if t == "plot_vector":
            return self._plot_vector(beat)
        if t == "rotate_vector":
            return self._rotate_vector(beat)
        if t == "show_multiplication":
            return self._show_multiplication(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _plane_appear(self, beat: dict) -> list[str]:
        shift = ".shift(DOWN * 0.5)" if self.title else ""
        return [
            "cp = ComplexPlane()",
            f"cp.set_width(10).center(){shift}",
            'cp_re_label = Text("Re", font_size=28, color=GREY_A).next_to(cp.x_axis, RIGHT, buff=0.2)',
            'cp_im_label = Text("Im", font_size=28, color=GREY_A).next_to(cp.y_axis, UP, buff=0.2)',
            "self.play(ShowCreation(cp), Write(cp_re_label), Write(cp_im_label))",
            "self.wait(0.5)",
        ]

    def _plot_point(self, beat: dict) -> list[str]:
        re = beat.get("re", 1.0)
        im = beat.get("im", 1.0)
        label = beat.get("label", f"{re}+{im}i")
        color = beat.get("color", "YELLOW")
        escaped = label.replace('"', '\\"')
        return [
            f"_cp_pt = Dot(cp.n2p(complex({re}, {im})), color={color}, radius=0.1)",
            f'_cp_pt_label = Text("{escaped}", font_size=24, color={color})',
            "_cp_pt_label.next_to(_cp_pt, UR, buff=0.1)",
            "self.play(GrowFromCenter(_cp_pt), Write(_cp_pt_label))",
            "self.wait(0.5)",
        ]

    def _plot_vector(self, beat: dict) -> list[str]:
        re = beat.get("re", 1.0)
        im = beat.get("im", 1.0)
        color = beat.get("color", "BLUE")
        return [
            f"_cp_vec = Arrow(cp.n2p(0), cp.n2p(complex({re}, {im})), color={color}, buff=0)",
            "self.play(GrowArrow(_cp_vec))",
            "self.wait(0.5)",
        ]

    def _rotate_vector(self, beat: dict) -> list[str]:
        re = beat.get("re", 1.0)
        im = beat.get("im", 0.0)
        angle_deg = beat.get("angle_degrees", 90)
        duration = beat.get("duration", 1.5)
        return [
            f"_cp_rvec = Arrow(cp.n2p(0), cp.n2p(complex({re}, {im})), color=GREEN, buff=0)",
            "self.play(GrowArrow(_cp_rvec))",
            f"self.play(_cp_rvec.animate.rotate({angle_deg} * DEGREES, about_point=cp.n2p(0)), run_time={duration})",
            "self.wait(0.5)",
        ]

    def _show_multiplication(self, beat: dict) -> list[str]:
        z1 = beat.get("z1", [1, 0])
        z2 = beat.get("z2", [0, 1])
        return [
            f"_z1 = complex({z1[0]}, {z1[1]})",
            f"_z2 = complex({z2[0]}, {z2[1]})",
            "_z_prod = _z1 * _z2",
            "_cp_v1 = Arrow(cp.n2p(0), cp.n2p(_z1), color=BLUE, buff=0)",
            "_cp_v2 = Arrow(cp.n2p(0), cp.n2p(_z2), color=RED, buff=0)",
            "_cp_vp = Arrow(cp.n2p(0), cp.n2p(_z_prod), color=GREEN, buff=0)",
            "self.play(GrowArrow(_cp_v1), GrowArrow(_cp_v2))",
            "self.wait(0.5)",
            "self.play(ReplacementTransform(_cp_v1.copy(), _cp_vp))",
            "self.wait(1.0)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        position = beat.get("position", "right")
        escaped = text.replace('"', '\\"')
        pos_map = {
            "right": "RIGHT * 4.5",
            "top": "UP * 3.0",
        }
        pos = pos_map.get(position, "RIGHT * 4.5")
        return [
            f'_cp_ann = Text("{escaped}", font_size=28, color=WHITE)',
            f"_cp_ann.move_to({pos})",
            "self.play(Write(_cp_ann))",
            "self.wait(1.0)",
        ]
