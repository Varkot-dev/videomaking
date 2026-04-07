"""
Solid3DTemplate — generates ManimGL scenes for 3D solid objects.
Beat types: solid_appear, rotate, rotate_camera, annotation.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class Solid3DTemplate(TemplateScene):

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
            "_solid = None",
            "",
        ]

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "solid_appear":
            return self._solid_appear(beat)
        if t == "rotate":
            return self._rotate(beat)
        if t == "rotate_camera":
            return self._rotate_camera(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _solid_appear(self, beat: dict) -> list[str]:
        shape = beat.get("shape", "sphere")
        params = beat.get("params", {})
        color = normalize_color(beat.get("color", "BLUE"))

        shape_map = {
            "sphere": f"Sphere(radius={params.get('radius', 1.5)})",
            "torus": f"Torus(r1={params.get('r1', 1.5)}, r2={params.get('r2', 0.5)})",
            "cylinder": f"Cylinder(radius={params.get('radius', 0.8)}, height={params.get('height', 2.0)})",
            "cone": f"Cone(base_radius={params.get('base_radius', 1.0)}, height={params.get('height', 2.0)})",
            "cube": f"Cube(side_length={params.get('side_length', 2.0)})",
        }
        creator = shape_map.get(shape, "Sphere(radius=1.5)")

        return [
            f"_solid = {creator}",
            # set_style() doesn't exist on 3D objects — use set_color() and set_opacity()
            f"_solid.set_color({color})",
            "_solid.set_opacity(0.85)",
            "self.play(ShowCreation(_solid), run_time=1.5)",
            "self.wait(0.5)",
        ]

    def _rotate(self, beat: dict) -> list[str]:
        axis = beat.get("axis", "y")
        angle_deg = beat.get("angle_degrees", 180)
        duration = beat.get("duration", 2.0)
        axis_map = {"x": "RIGHT", "y": "UP", "z": "OUT"}
        axis_vec = axis_map.get(axis, "UP")
        return [
            f"self.play(_solid.animate.rotate({angle_deg} * DEGREES, axis={axis_vec}), run_time={duration})",
            "self.wait(0.5)",
        ]

    def _rotate_camera(self, beat: dict) -> list[str]:
        delta_theta = beat.get("delta_theta", 45)
        delta_phi = beat.get("delta_phi", 0)
        duration = beat.get("duration", 2.0)
        # ManimGL increment_euler_angles uses dtheta/dphi, not theta/phi
        return [
            f"self.play(self.frame.animate.increment_euler_angles(dtheta={delta_theta} * DEGREES, dphi={delta_phi} * DEGREES), run_time={duration})",
            "self.wait(0.3)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        escaped = text.replace('"', '\\"')
        return [
            f'_solid_ann = Text("{escaped}", font_size=28, color=WHITE)',
            "_solid_ann.to_corner(DR, buff=0.5)",
            "self.add(_solid_ann)",
            "self.wait(1.0)",
        ]
