from manimlib import *


class StaggerBuildScene(Scene):
    """
    Pattern: Staggered element-by-element reveal — for arrays, lists, steps,
    or any sequence where items should appear one after another.

    Key techniques demonstrated:
    - LaggedStart(*[anim for el in elements], lag_ratio=0.15)
    - SurroundingRectangle sweep across elements (ValueTracker-driven)
    - VGroup.arrange(RIGHT/DOWN) to lay out uniform elements
    - Highlighting one element while others dim with set_opacity

    Use when narration says "one by one", "step by step", "each element",
    "scanning", or "in sequence".
    """

    def construct(self):
        title = Text("Linear scan vs binary search", font_size=44).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)

        # Build an array of 10 boxes with values
        values = [3, 7, 11, 15, 22, 31, 42, 58, 67, 74]
        boxes = VGroup(*[
            VGroup(
                Square(side_length=0.75, color=GREY_B, fill_color="#1C1C1C", fill_opacity=1),
                Text(str(v), font_size=24, color=WHITE),
            )
            for v in values
        ])
        for box in boxes:
            box[1].move_to(box[0])
        boxes.arrange(RIGHT, buff=0.1).center().shift(DOWN * 0.3)

        # Staggered reveal — each box fades in with a slight delay
        self.play(
            LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.12),
            run_time=1.8,
        )
        self.wait(0.3)

        # Linear scan — highlight sweeps left to right
        target_val = 42
        target_idx = values.index(target_val)
        scan_rect = SurroundingRectangle(boxes[0], color=YELLOW, buff=0.04)
        self.play(ShowCreation(scan_rect), run_time=0.3)

        for i in range(1, target_idx + 1):
            self.play(
                scan_rect.animate.move_to(boxes[i]),
                run_time=0.18,
                rate_func=linear,
            )

        # Found it — flash green
        self.play(
            boxes[target_idx][0].animate.set_color(GREEN),
            boxes[target_idx][1].animate.set_color(GREEN),
            run_time=0.4,
        )
        found_label = Text(f"Found {target_val} at index {target_idx}", font_size=30, color=GREEN)
        found_label.next_to(boxes, DOWN, buff=0.5)
        self.play(Write(found_label), run_time=0.6)
        self.wait(0.5)

        # Reset — show binary search approach
        self.play(
            FadeOut(scan_rect),
            FadeOut(found_label),
            *[b[0].animate.set_color(GREY_B) for b in boxes],
            *[b[1].animate.set_color(WHITE) for b in boxes],
            run_time=0.5,
        )

        # Binary search: mid = 4 (value 22), target > 22 → right half
        mid_idx = len(values) // 2 - 1  # index 4
        mid_rect = SurroundingRectangle(boxes[mid_idx], color=BLUE, buff=0.04)
        mid_label = Text("mid", font_size=22, color=BLUE).next_to(boxes[mid_idx], UP, buff=0.15)

        self.play(ShowCreation(mid_rect), Write(mid_label), run_time=0.5)
        # Dim left half — target is in right half
        self.play(
            *[b.animate.set_opacity(0.25) for b in boxes[:mid_idx + 1]],
            run_time=0.6,
        )
        self.wait(0.4)

        bs_label = Text("Binary: O(log n) — 3 steps max", font_size=28, color=BLUE)
        bs_label.next_to(boxes, DOWN, buff=0.5)
        self.play(Write(bs_label), run_time=0.7)
        self.wait(0.8)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
