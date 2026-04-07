"""
Parametric3DTemplate — generates ManimGL scenes for 3D parametric curves.
Beat types: axes_appear, curve_appear, rotate_camera.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class Parametric3DTemplate(TemplateScene):

    SCENE_BASE_CLASS = "ThreeDScene"

    def _render_title(self) -> list[str]:
        escaped = self.title.replace('"', '\\"')
        return [
            f'title = Text("{escaped}", font_size=40, color=BLUE)',
            "title.to_corner(UL, buff=0.5)",
            "self.add(title)",
        ]

    def scene_setup(self) -> list[str]:
        return [
            "self.frame.set_euler_angles(theta=-30 * DEGREES, phi=70 * DEGREES)",
            "",
        ]

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "axes_appear":
            return self._axes_appear(beat)
        if t == "curve_appear":
            return self._curve_appear(beat)
        if t == "rotate_camera":
            return self._rotate_camera(beat)
        return []

    def _axes_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-3, 3, 1])
        y_range = beat.get("y_range", [-3, 3, 1])
        z_range = beat.get("z_range", [-3, 3, 1])
        return [
            "p3d_axes = ThreeDAxes(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            f"    z_range={z_range},",
            ")",
            "self.play(ShowCreation(p3d_axes))",
            "self.wait(0.5)",
        ]

    def _curve_appear(self, beat: dict) -> list[str]:
        expr_str = beat.get("expr_str", "[np.cos(t), np.sin(t), t/3]")
        t_range = beat.get("t_range", [0, 6.28])
        color = normalize_color(beat.get("color", "YELLOW"))
        # ManimGL ParametricCurve requires t_range=(start, end, step) — 3 elements.
        # If spec provides only 2, add a default step of 0.05.
        if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
            t_range_arg = f"({t_range[0]}, {t_range[1]}, 0.05)"
        else:
            t_range_arg = str(tuple(t_range))
        return [
            "p3d_curve = ParametricCurve(",
            f"    lambda t: p3d_axes.c2p(*({expr_str})),",
            f"    t_range={t_range_arg},",
            f"    color={color},",
            ")",
            "self.play(ShowCreation(p3d_curve), run_time=2.0)",
            "self.wait(0.5)",
        ]

    def _rotate_camera(self, beat: dict) -> list[str]:
        delta_theta = beat.get("delta_theta", 45)
        duration = beat.get("duration", 2.0)
        # ManimGL uses dtheta, not theta
        return [
            f"self.play(self.frame.animate.increment_euler_angles(dtheta={delta_theta} * DEGREES), run_time={duration})",
            "self.wait(0.3)",
        ]
