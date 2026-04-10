"""
Smoke tests that replay Section03 and Section04 scenes from the bubble-sort
pipeline run through the full precheck → codeguard → classify → guidance chain.

These tests do NOT call manimgl. They verify that:
- The auto-fixes in codeguard handle the patterns found in generated code.
- The retry guidance is correct for precheck errors.
- The array_swap example used as Director reference passes precheck.

Run with:
    python3 -m pytest tests/test_smoke_sections.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from manimgen.validator.codeguard import (
    validate_scene_code,
    precheck_and_autofix,
    apply_known_fixes,
    _detect_tmt_on_text,
)
from manimgen.validator.retry import _classify_error, _fix_guidance


# ── Load actual failing scenes from logs ─────────────────────────────────────

def _load_log(filename: str) -> str:
    here = os.path.dirname(__file__)
    log_path = os.path.join(here, "..", "manimgen", "output", "logs", filename)
    if not os.path.isfile(log_path):
        pytest.skip(f"Log file not found (run the pipeline first): {log_path}")
    with open(log_path) as f:
        return f.read()


# ── Inline pattern tests — not tied to specific pipeline run output ───────────

# Inline examples of patterns that codeguard must detect/fix.
# These are stable regardless of what the LLM most recently generated.

_BECOME_INSIDE_PLAY_CODE = """\
from manimlib import *
class S(Scene):
    def construct(self):
        boxes = VGroup(*[Square() for _ in range(5)]).arrange(RIGHT)
        box_list = list(boxes)
        scan_rect = SurroundingRectangle(box_list[0], color=YELLOW, buff=0.05)
        self.play(ShowCreation(scan_rect), run_time=0.3)
        for i in range(1, 5):
            self.play(scan_rect.become(SurroundingRectangle(box_list[i], color=YELLOW, buff=0.05)), run_time=0.2)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
"""

_GET_TEX_STRING_CODE = """\
from manimlib import *
class S(Scene):
    def construct(self):
        labels = VGroup(*[Tex(str(v)) for v in [5, 3, 8]])
        label_list = list(labels)
        for i in range(2):
            val = int(label_list[i].get_tex_string())
            if val > 3:
                self.play(label_list[i].animate.set_color(RED))
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
"""


class TestSection03ScenePrecheck:
    """Tests for patterns that codeguard must detect and fix.

    Uses inline code samples (not log files) for stability — these tests verify
    the capability regardless of what the LLM most recently generated.
    """

    def test_plain_list_swap_not_flagged(self):
        """current_boxes[i], current_boxes[i+1] = ... must not produce a VGroup error.

        current_boxes and current_labels are Python lists (list(boxes)), not VGroups.
        Their swap is valid Python and must not trigger any VGroup error.
        """
        plain_list_lines = [
            "current_boxes[i], current_boxes[i+1] = current_boxes[i+1], current_boxes[i]",
            "current_labels[i], current_labels[i+1] = current_labels[i+1], current_labels[i]",
        ]
        for line in plain_list_lines:
            line_errors = validate_scene_code(line)
            plain_vgroup = [e for e in line_errors if "VGroup" in e]
            assert plain_vgroup == [], (
                f"Plain list swap line triggers VGroup error: {line!r}\nErrors: {plain_vgroup}"
            )

    def test_become_inside_play_is_auto_fixed(self):
        """self.play(scan_rect.become(SurroundingRectangle(...))) must be rewritten."""
        fixed, applied = apply_known_fixes(_BECOME_INSIDE_PLAY_CODE)
        become_applied = [a for a in applied if "become" in a]
        assert become_applied, (
            "apply_known_fixes must have rewritten self.play(obj.become(...)) to "
            "become()+ShowCreation. "
            f"Applied: {applied}"
        )

    def test_become_inside_play_removed_after_fix(self):
        """After fix, no self.play(x.become(...)) should remain."""
        fixed, _ = apply_known_fixes(_BECOME_INSIDE_PLAY_CODE)
        import re
        remaining = re.findall(r"self\.play\(\s*\w+\.become\(", fixed)
        assert remaining == [], (
            f"self.play(obj.become(...)) still present after fix: {remaining}"
        )

    def test_get_tex_string_is_caught_by_precheck(self):
        """get_tex_string() must be detected as a banned pattern."""
        errors = validate_scene_code(_GET_TEX_STRING_CODE)
        tex_errors = [e for e in errors if "get_tex_string" in e]
        assert tex_errors, "get_tex_string() must be flagged by validate_scene_code"

    def test_get_tex_string_error_mentions_python_list(self):
        """The error message must tell the LLM to use a Python list."""
        errors = validate_scene_code(_GET_TEX_STRING_CODE)
        tex_errors = [e for e in errors if "get_tex_string" in e]
        assert tex_errors, "get_tex_string error missing"
        assert any("list" in e.lower() or "current_values" in e for e in tex_errors), (
            "Error message must mention 'list' or 'current_values' to guide the LLM fix. "
            f"Got: {tex_errors}"
        )

    def test_precheck_error_classified_as_precheck_vgroup(self):
        """The precheck error string must map to 'precheck_vgroup', not 'type'."""
        precheck_stderr = (
            "Precheck failed:\n"
            "- VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
            "Use a Python list for mutable indexed storage, then rebuild: "
            "items[i] = new_mob; group = VGroup(*items)."
        )
        assert _classify_error(precheck_stderr) == "precheck_vgroup", (
            "LLM will get wrong guidance — must be precheck_vgroup, not 'type'"
        )

    def test_guidance_tells_llm_about_parallel_list(self):
        guidance = _fix_guidance("precheck_vgroup")
        assert "box_list" in guidance or "list(boxes)" in guidance

    def test_correctly_fixed_code_passes_precheck(self):
        """A scene using box_list correctly must have zero VGroup precheck errors."""
        correct_code = '''from manimlib import *

class Section03Scene(Scene):
    def construct(self):
        values = [5, 1, 4, 8, 2, 7]
        boxes = VGroup(*[
            Square(side_length=0.9, fill_color="#2a2a2a", fill_opacity=1,
                   stroke_width=2, color=GREY_B)
            for _ in values
        ]).arrange(RIGHT, buff=0.15).center()
        labels = VGroup(*[
            Text(str(v), font_size=28, color=WHITE).move_to(boxes[k])
            for k, v in enumerate(values)
        ])

        box_list = list(boxes)
        label_list = list(labels)
        current_values = list(values)

        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.15), run_time=1.0)
        self.play(LaggedStart(*[FadeIn(l) for l in labels], lag_ratio=0.15), run_time=0.8)
        self.wait(1.0)

        for i in range(len(values) - 1):
            if current_values[i] > current_values[i + 1]:
                pos_i = box_list[i].get_center()
                pos_j = box_list[i + 1].get_center()
                self.play(
                    box_list[i].animate.move_to(pos_j),
                    box_list[i + 1].animate.move_to(pos_i),
                    label_list[i].animate.move_to(pos_j),
                    label_list[i + 1].animate.move_to(pos_i),
                    run_time=0.55,
                )
                box_list[i], box_list[i + 1] = box_list[i + 1], box_list[i]
                label_list[i], label_list[i + 1] = label_list[i + 1], label_list[i]
                current_values[i], current_values[i + 1] = current_values[i + 1], current_values[i]
            self.wait(0.2)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
'''
        fixed = precheck_and_autofix(correct_code)
        errors = validate_scene_code(fixed)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], (
            "Correctly-written scene using box_list still fails precheck:\n"
            + "\n".join(vgroup_errors)
        )


# ── Section04 — set_fill_color and get_tex_string auto-fix ───────────────────

_SET_FILL_COLOR_CODE = """\
from manimlib import *
class S(Scene):
    def construct(self):
        boxes = VGroup(*[Square() for _ in range(3)]).arrange(RIGHT)
        box_list = list(boxes)
        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.1), run_time=0.8)
        self.play(box_list[0].animate.set_fill_color(GREEN), run_time=0.4)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
"""

class TestSection04ScenePrecheck:
    """Tests for patterns seen in multi-pass bubble sort scenes.

    Uses inline code samples (not log files) for stability.
    """

    def test_become_inside_play_is_auto_fixed(self):
        """self.play(scan_rect.become(SurroundingRectangle(...))) must be rewritten."""
        fixed, applied = apply_known_fixes(_BECOME_INSIDE_PLAY_CODE)
        become_applied = [a for a in applied if "become" in a]
        assert become_applied, (
            "apply_known_fixes must have rewritten self.play(obj.become(...)) to "
            "become()+ShowCreation. "
            f"Applied: {applied}"
        )

    def test_become_inside_play_removed_after_fix(self):
        """After fix, no self.play(x.become(...)) remains (only .animate.become stays)."""
        fixed, _ = apply_known_fixes(_BECOME_INSIDE_PLAY_CODE)
        import re
        remaining = re.findall(r"self\.play\(\s*\w+\.become\(", fixed)
        assert remaining == [], (
            f"self.play(obj.become(...)) still present after fix: {remaining}"
        )

    def test_set_fill_color_is_auto_fixed(self):
        """set_fill_color() must be rewritten to set_fill()."""
        fixed, applied = apply_known_fixes(_SET_FILL_COLOR_CODE)
        fill_applied = [a for a in applied if "set_fill" in a]
        assert fill_applied, (
            f"apply_known_fixes must rewrite set_fill_color -> set_fill. Applied: {applied}"
        )
        assert "set_fill_color" not in fixed, "set_fill_color still present after fix"

    def test_set_fill_color_detected_by_validator(self):
        """set_fill_color() must be flagged as a banned pattern."""
        errors = validate_scene_code(_SET_FILL_COLOR_CODE)
        fill_errors = [e for e in errors if "set_fill" in e]
        assert fill_errors, "set_fill_color() must be flagged by validate_scene_code"

    def test_no_tmt_on_text_in_inline_code(self):
        """The inline become code has no TransformMatchingTex on Text — verify no false positive."""
        fixed, _ = apply_known_fixes(_BECOME_INSIDE_PLAY_CODE)
        remaining = _detect_tmt_on_text(fixed)
        assert remaining == [], (
            "False positive TMT detection on become-fix code:\n"
            + "\n".join(remaining)
        )


# ── array_swap_scene.py must itself pass precheck ────────────────────────────

class TestArraySwapExamplePassesPrecheck:
    """The example we give to the Director must pass precheck.
    If it doesn't, we're teaching the Director broken patterns.
    """

    def test_example_has_no_vgroup_errors(self):
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "examples", "array_swap_scene.py")
        if not os.path.isfile(path):
            pytest.skip("examples/array_swap_scene.py not found")
        with open(path) as f:
            code = f.read()
        fixed = precheck_and_autofix(code)
        errors = validate_scene_code(fixed)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], (
            "array_swap_scene.py (Director reference) fails precheck:\n"
            + "\n".join(vgroup_errors)
        )
