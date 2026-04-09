"""
Smoke tests that replay the exact Section03 and Section04 scenes from the
2026-04-08 bubble-sort run through the full precheck → codeguard → classify
→ guidance chain.

These tests do NOT call manimgl. They verify that the scenes which were
previously falling back now pass precheck and get correct retry guidance.

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


# ── Section03 — VGroup swap pattern ──────────────────────────────────────────

class TestSection03ScenePrecheck:
    """Section03Scene_attempt1.py: bubble sort with VGroup tuple swap on line 77.

    The scene is architecturally nearly correct — it uses current_values (plain list)
    for logical tracking. Only boxes[i], boxes[i+1] = boxes[i+1], boxes[i] is bad.
    The plain-list lines must NOT be flagged. The real VGroup line still must be.
    """

    def test_plain_list_swap_not_flagged(self):
        """current_values[i], current_values[i+1] = ... must not produce a VGroup error.

        Section03 has one genuinely bad line (boxes[i], boxes[i+1] = ...) which fires
        both the tuple-swap and single-assign patterns — so 2 errors are expected.
        But the current_values plain-list lines must not add any extra errors.
        """
        code = _load_log("Section03Scene_attempt1.py")
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]

        # Verify it's ONLY the boxes[] line firing — not current_values or anything else.
        # The single bad VGroup line can match both patterns (tuple-swap + single-assign),
        # so 1-2 errors is correct. Any more means plain list lines are being flagged.
        import re
        plain_list_lines = [
            "current_values[i], current_values[i+1] = current_values[i+1], current_values[i]"
        ]
        for line in plain_list_lines:
            line_errors = validate_scene_code(line)
            plain_vgroup = [e for e in line_errors if "VGroup" in e]
            assert plain_vgroup == [], (
                f"Plain list line triggers VGroup error: {line!r}\nErrors: {plain_vgroup}"
            )
        # The actual scene should have at most 2 VGroup errors (boxes[] line matching both patterns)
        assert len(vgroup_errors) <= 2, (
            f"Got {len(vgroup_errors)} VGroup errors — too many, plain list lines may be flagged:\n"
            + "\n".join(vgroup_errors)
        )

    def test_vgroup_swap_line_is_still_caught(self):
        """boxes[i], boxes[i+1] = boxes[i+1], boxes[i] on line 77 must still be flagged."""
        code = _load_log("Section03Scene_attempt1.py")
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors, "The real VGroup swap on boxes[] must still be detected"

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


# ── Section04 — TransformMatchingTex on Text ─────────────────────────────────

class TestSection04ScenePrecheck:
    """Section04Scene_attempt1.py: multi-pass bubble sort with TMT on Text objects.

    Lines 102 and 124 use TransformMatchingTex on Text() counter labels.
    After apply_known_fixes(), both must be converted to FadeOut/FadeIn.
    """

    def test_tmt_on_text_is_auto_fixed(self):
        code = _load_log("Section04Scene_attempt1.py")
        fixed, applied = apply_known_fixes(code)
        tmt_applied = [a for a in applied if "TransformMatchingTex" in a]
        assert tmt_applied, (
            "apply_known_fixes must have converted TransformMatchingTex(Text, ...) to FadeOut/FadeIn. "
            f"Applied: {applied}"
        )

    def test_no_tmt_on_text_remains_after_fix(self):
        code = _load_log("Section04Scene_attempt1.py")
        fixed, _ = apply_known_fixes(code)
        remaining = _detect_tmt_on_text(fixed)
        assert remaining == [], (
            "TransformMatchingTex on Text still present after fix:\n"
            + "\n".join(remaining)
        )

    def test_tmt_errors_gone_after_fix_leaving_only_vgroup(self):
        """After apply_known_fixes, Section04 TMT errors are gone.

        The VGroup errors on labels[j] remain (structural rewrite required — the LLM
        must fix these using the retry prompt). But no TMT-on-Text errors remain.
        This verifies the auto-fix did its job without breaking other detection.
        """
        code = _load_log("Section04Scene_attempt1.py")
        fixed, _ = apply_known_fixes(code)
        # TMT errors must be gone
        tmt_errors = [e for e in validate_scene_code(fixed) if "TransformMatchingTex" in e]
        assert tmt_errors == [], (
            "TMT errors still present after apply_known_fixes:\n" + "\n".join(tmt_errors)
        )
        # Verify labels[j] VGroup errors are still present (correct — needs LLM fix)
        vgroup_errors = [e for e in validate_scene_code(fixed) if "VGroup" in e]
        assert vgroup_errors, (
            "Expected VGroup errors on labels[j] to remain (needs LLM fix) — none found"
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
