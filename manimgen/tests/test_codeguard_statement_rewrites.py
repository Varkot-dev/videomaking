"""R29 (#85): codeguard statement rewrites must not drop animations or misplace code."""

import ast

from manimgen.validator.codeguard import (
    _fix_broken_call_args,
    _inject_color_role_header,
    apply_error_aware_fixes,
    apply_known_fixes,
    precheck_and_autofix,
)


def _known(code: str) -> str:
    return apply_known_fixes(code)[0]


class TestBecomeInsidePlay:
    def test_other_animations_and_kwargs_are_kept(self):
        out = _known("self.play(r.become(Circle()), FadeIn(c), run_time=1)\n")
        assert out == "self.play(r.animate.become(Circle()), FadeIn(c), run_time=1)\n"

    def test_multiline_play_keeps_every_argument(self):
        code = (
            "self.play(\n"
            "    scan_rect.become(SurroundingRectangle(boxes[i], color=YELLOW)),\n"
            "    FadeOut(old),  # drop the old one\n"
            "    run_time=0.25,\n"
            "    rate_func=linear,\n"
            ")\n"
        )
        out = _known(code)
        assert out == code.replace("scan_rect.become", "scan_rect.animate.become")

    def test_attribute_and_subscript_targets(self):
        out = _known("self.play(a.b.become(x), boxes[i].become(y))\n")
        assert out == "self.play(a.b.animate.become(x), boxes[i].animate.become(y))\n"

    def test_animate_become_is_untouched(self):
        code = "self.play(r.animate.become(Circle()), run_time=1)\n"
        assert _known(code) == code

    def test_become_outside_play_is_untouched(self):
        code = "counter.become(new_counter)\nself.play(FadeIn(counter))\n"
        assert _known(code) == code

    def test_become_nested_in_another_animation_is_untouched(self):
        code = "self.play(Transform(a, b.become(c)))\n"
        assert _known(code) == code

    def test_result_compiles_and_no_play_args_lost(self):
        code = "self.play(r.become(Circle()), FadeIn(c), run_time=1)\n"
        before = ast.parse(code).body[0].value
        after = ast.parse(_known(code)).body[0].value
        assert len(after.args) == len(before.args)
        assert [k.arg for k in after.keywords] == ["run_time"]


class TestColorRoleHeaderPlacement:
    def test_indented_import_is_not_a_top_level_import(self):
        code = (
            "from manimlib import *\n\n\n"
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        import math\n"
            "        self.add(Square(color=MUTED))\n"
        )
        out, label = _inject_color_role_header(code)
        assert label
        ast.parse(out)
        assert out.index("MUTED = GREY_A") < out.index("class S")

    def test_header_goes_after_the_last_top_level_import(self):
        code = (
            "import numpy as np\n"
            "from manimlib import *\n"
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        self.add(Square(color=MUTED))\n"
        )
        out, _ = _inject_color_role_header(code)
        ast.parse(out)
        assert (
            out.index("from manimlib")
            < out.index("MUTED = GREY_A")
            < out.index("class S")
        )

    def test_multiline_import_is_not_split(self):
        code = (
            "from manimlib import (\n"
            "    Scene,\n"
            "    Square,\n"
            ")\n"
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        self.add(Square(color=MUTED))\n"
        )
        out, _ = _inject_color_role_header(code)
        ast.parse(out)

    def test_defined_role_is_not_injected(self):
        code = "from manimlib import *\nMUTED = GREY_B\nx = MUTED\n"
        assert _inject_color_role_header(code) == (code, None)


class TestSetCameraOrientationReplacement:
    def test_unparseable_call_does_not_comment_out_the_rest_of_the_line(self):
        code = "self.set_camera_orientation(foo); self.wait(1)\n"
        out = _known(code)
        assert "set_camera_orientation" not in out
        assert "self.wait(1)" in out
        ast.parse(out)
        assert "#" not in out

    def test_nested_parentheses_are_replaced_whole(self):
        code = "self.set_camera_orientation(phi=np.radians(60), theta=0)\n"
        out = _known(code)
        ast.parse(out)
        assert "set_camera_orientation" not in out

    def test_call_used_as_a_value_becomes_none_not_pass(self):
        out = _known("x = self.set_camera_orientation(foo)\n")
        assert out == "x = None\n"

    def test_multiline_call_is_rewritten(self):
        code = "self.set_camera_orientation(\n    phi=60 * DEGREES,\n    theta=-45 * DEGREES,\n)\n"
        assert _known(code) == "self.frame.reorient(-45, 60)\n"

    def test_parseable_form_still_rewritten(self):
        out = _known(
            "self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)\n"
        )
        assert out == "self.frame.reorient(-45, 60)\n"


class TestBeginAmbientCameraRotation:
    def test_multiline_call_is_rewritten(self):
        code = "self.begin_ambient_camera_rotation(\n    rate=0.2\n)\n"
        assert _known(code) == "self.frame.add_ambient_rotation(angular_speed=0.2)\n"

    def test_nested_parentheses(self):
        out = _known("self.begin_ambient_camera_rotation(rate=0.2); self.wait(2)\n")
        assert (
            out == "self.frame.add_ambient_rotation(angular_speed=0.2); self.wait(2)\n"
        )

    def test_bare_call(self):
        assert _known("self.begin_ambient_camera_rotation()\n") == (
            "self.frame.add_ambient_rotation()\n"
        )


class TestBrokenCallArgs:
    def test_valid_one_tuple_is_untouched(self):
        code = "pair = (title,)\nself.play(Write(title), run_time=1,)\n"
        assert _fix_broken_call_args(code) == (code, [])
        assert precheck_and_autofix(code) == code

    def test_leading_comma_still_fixed_when_code_does_not_compile(self):
        code = "ax.get_axis_labels(, y_label='y')\n"
        out, applied = _fix_broken_call_args(code)
        assert out == "ax.get_axis_labels(y_label='y')\n"
        assert applied

    def test_reorient_leading_comma_is_just_the_comma_fix(self):
        out, applied = _fix_broken_call_args("self.frame.reorient(, theta=-45)\n")
        assert out == "self.frame.reorient(theta=-45)\n"
        assert not any("reorient" in a for a in applied)

    def test_error_aware_path_leaves_valid_code_alone(self):
        code = "pair = (title,)\n"
        assert apply_error_aware_fixes(code, "")[0] == code
