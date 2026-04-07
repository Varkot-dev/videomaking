"""
Surface3DTemplate — generates ManimGL scenes for 3D surface plots.
Beat types: axes_appear, surface_appear, rotate_camera, trace_curve, annotation.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class Surface3DTemplate(TemplateScene):

    SCENE_BASE_CLASS = "ThreeDScene"

    def _render_title(self) -> list[str]:
        escaped = self.title.replace('"', '\\"')
        return [
            f'title = Text("{escaped}", font_size=40, color=BLUE)',
            "title.to_corner(UL, buff=0.5)",
            "self.add(title)",
        ]

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "axes_appear":
            return self._axes_appear(beat)
        if t == "surface_appear":
            return self._surface_appear(beat)
        if t == "rotate_camera":
            return self._rotate_camera(beat)
        if t == "trace_curve":
            return self._trace_curve(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def scene_setup(self) -> list[str]:
        return [
            "self.frame.set_euler_angles(theta=-30 * DEGREES, phi=70 * DEGREES)",
            "",
        ]

    def _axes_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-3, 3, 1])
        y_range = beat.get("y_range", [-3, 3, 1])
        z_range = beat.get("z_range", [-2, 2, 1])
        return [
            "axes3d = ThreeDAxes(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            f"    z_range={z_range},",
            ")",
            "self.play(ShowCreation(axes3d))",
            "self.wait(0.5)",
        ]

    def _surface_appear(self, beat: dict) -> list[str]:
        expr = beat.get("expr_str", "np.sin(x) * np.cos(y)")
        color = normalize_color(beat.get("color", "BLUE"))
        opacity = beat.get("opacity", 0.8)
        return [
            "surface = axes3d.get_parametric_surface(",
            f"    lambda x, y: axes3d.c2p(x, y, {expr}),",
            "    resolution=(30, 30),",
            f"    color={color},",
            f"    opacity={opacity},",
            ")",
            "self.play(ShowCreation(surface), run_time=2.0)",
            "self.wait(0.5)",
        ]

    def _rotate_camera(self, beat: dict) -> list[str]:
        delta_theta = beat.get("delta_theta", 30)
        delta_phi = beat.get("delta_phi", 0)
        duration = beat.get("duration", 2.0)
        # ManimGL increment_euler_angles uses dtheta/dphi, not theta/phi
        return [
            f"self.play(self.frame.animate.increment_euler_angles(dtheta={delta_theta} * DEGREES, dphi={delta_phi} * DEGREES), run_time={duration})",
            "self.wait(0.3)",
        ]

    def _trace_curve(self, beat: dict) -> list[str]:
        u_range = beat.get("u_range", [-3, 3])
        color = normalize_color(beat.get("color", "YELLOW"))
        duration = beat.get("duration", 2.0)
        # axes3d.get_parametric_curve takes function + **kwargs (no t_range arg)
        # Pass color via kwargs; use ParametricCurve directly for t_range control
        t_start = u_range[0] if isinstance(u_range, list) and len(u_range) >= 1 else -3
        t_end = u_range[1] if isinstance(u_range, list) and len(u_range) >= 2 else 3
        return [
            "_tc_curve = ParametricCurve(",
            f"    lambda t: axes3d.c2p(t, 0, np.sin(t)),",
            f"    t_range=({t_start}, {t_end}, 0.05),",
            f"    color={color},",
            ")",
            f"self.play(ShowCreation(_tc_curve), run_time={duration})",
            "self.wait(0.5)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        escaped = text.replace('"', '\\"')
        return [
            f'_3d_ann = Text("{escaped}", font_size=28, color=WHITE)',
            "_3d_ann.to_corner(DR, buff=0.5)",
            "self.add(_3d_ann)",
            "self.wait(1.0)",
        ]
