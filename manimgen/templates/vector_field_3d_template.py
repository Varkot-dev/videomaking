"""
VectorField3DTemplate — generates ManimGL scenes for 3D vector fields.
Beat types: axes_appear, vector_field, flow_particle, rotate_camera.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class VectorField3DTemplate(TemplateScene):

    SCENE_BASE_CLASS = "ThreeDScene"

    def __init__(self, spec: dict):
        super().__init__(spec)
        self._vf_func_str: str | None = None  # store for flow_particle to reuse

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
        if t == "vector_field":
            return self._vector_field(beat)
        if t == "flow_particle":
            return self._flow_particle(beat)
        if t == "rotate_camera":
            return self._rotate_camera(beat)
        return []

    def _axes_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-3, 3, 1])
        y_range = beat.get("y_range", [-3, 3, 1])
        z_range = beat.get("z_range", [-3, 3, 1])
        return [
            "vf_axes = ThreeDAxes(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            f"    z_range={z_range},",
            ")",
            "self.play(ShowCreation(vf_axes))",
            "self.wait(0.5)",
        ]

    def _vector_field(self, beat: dict) -> list[str]:
        func_str = beat.get("func_str", "[-y, x, 0]")
        color = normalize_color(beat.get("color", "BLUE"))
        self._vf_func_str = func_str
        # ManimGL VectorField requires coordinate_system as second arg.
        # The func receives a 2D point from the axes; unpack x,y only.
        return [
            "def _vf_func(p):",
            "    x, y = p[0], p[1]",
            f"    out = np.array({func_str})",
            "    return out[:2]",
            f"_vf = VectorField(_vf_func, coordinate_system=vf_axes, color={color})",
            "self.play(ShowCreation(_vf), run_time=2.0)",
            "self.wait(0.5)",
        ]

    def _flow_particle(self, beat: dict) -> list[str]:
        start = beat.get("start", [1, 0, 0])
        color = normalize_color(beat.get("color", "YELLOW"))
        duration = beat.get("duration", 3.0)
        func_str = self._vf_func_str or "[-y, x, 0]"
        # Build a simple Euler-integrated path from the vector field function.
        # t_range needs 3 elements for ParametricCurve: (start, end, step).
        return [
            f"_fp_start = np.array({start}, dtype=float)",
            "def _fp_euler_path(t):",
            "    p = np.array(_fp_start[:2])",
            f"    steps = int(t / 0.05)",
            "    for _ in range(steps):",
            "        x, y = p[0], p[1]",
            f"        dv = np.array({func_str})[:2]",
            "        norm = np.linalg.norm(dv)",
            "        p = p + (dv / max(norm, 0.01)) * 0.05",
            "    return np.array([p[0], p[1], 0])",
            f"_fp_path = ParametricCurve(_fp_euler_path, t_range=(0, {duration}, 0.05), color={color})",
            f"_fp = Dot(_fp_start, color={color}, radius=0.12)",
            "self.add(_fp_path)",
            f"self.play(MoveAlongPath(_fp, _fp_path), run_time={duration})",
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
