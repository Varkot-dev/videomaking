from manimlib import *


class ArraySwapScene(Scene):
    """
    techniques: array_swap, sweep_highlight
    Demonstrates animating element swaps (bubble sort) with correct role colors.

    Palette roles used:
    - GREY_B fill / stroke: default box state (STRUCT)
    - TEAL_A stroke: scanning / active comparison (PRIMARY)
    - GOLD stroke: swapping elements (SECONDARY)
    - GREEN fill: sorted / finalized elements (SUCCESS)

    CRITICAL PATTERN — VGroup does NOT support item assignment.
    Swapping indices directly on a VGroup crashes with TypeError at runtime.

    CORRECT APPROACH — use a parallel Python list for index tracking:
        box_list = list(boxes)                     # mutable Python list
        box_list[i], box_list[j] = box_list[j], box_list[i]  # swap the list

    The VGroup is used ONLY for initial layout arrangement.
    After any swap, use the Python list for position lookups — never index into VGroup.
    """

    def construct(self):
        # Archetype D
        values = [64, 34, 25, 12, 22, 11, 90]

        title = Text("Bubble Sort", font_size=48, color=WHITE).to_edge(UP, buff=0.8)

        # Build boxes as VGroup for layout only. Default state: GREY_B stroke, dark fill.
        boxes = VGroup(*[
            Square(side_length=0.85, fill_color="#2a2a2a", fill_opacity=1,
                   stroke_width=2.5, color=GREY_B)
            for _ in values
        ]).arrange(RIGHT, buff=0.14).center()

        labels = VGroup(*[
            Text(str(v), font_size=26, color=WHITE).move_to(boxes[i])
            for i, v in enumerate(values)
        ])

        # CUE 0 — 3.0s: Reveal the array
        self.play(Write(title), run_time=0.6)
        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.08), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(l) for l in labels], lag_ratio=0.08), run_time=0.6)
        self.wait(1.0)  # 0.6 + 0.8 + 0.6 + 1.0 = 3.0 ✓

        # CRITICAL: parallel Python lists for index tracking after swaps
        box_list = list(boxes)
        label_list = list(labels)
        current_values = list(values)  # track logical values for comparisons

        # scanning highlight rect — TEAL_A = PRIMARY (active comparison)
        scan_rect = SurroundingRectangle(box_list[0], color=TEAL_A, buff=0.06, stroke_width=2.5)
        self.play(ShowCreation(scan_rect), run_time=0.3)

        def animate_swap(i, j):
            """Animate swap of positions i and j. Returns total animation time consumed."""
            # Show scanning in TEAL_A (PRIMARY — active comparison)
            scan_rect.become(SurroundingRectangle(box_list[i], color=TEAL_A, buff=0.06, stroke_width=2.5))
            self.play(ShowCreation(scan_rect), run_time=0.25)
            scan_rect.become(SurroundingRectangle(box_list[j], color=TEAL_A, buff=0.06, stroke_width=2.5))
            self.play(ShowCreation(scan_rect), run_time=0.25)

            # Highlight swapping pair in GOLD (SECONDARY — swap accent)
            self.play(
                box_list[i].animate.set_stroke(color=GOLD, width=3),
                box_list[j].animate.set_stroke(color=GOLD, width=3),
                run_time=0.3,
            )

            pos_i = box_list[i].get_center()
            pos_j = box_list[j].get_center()

            # Animate boxes AND labels to each other's positions
            self.play(
                box_list[i].animate.move_to(pos_j),
                box_list[j].animate.move_to(pos_i),
                label_list[i].animate.move_to(pos_j),
                label_list[j].animate.move_to(pos_i),
                run_time=0.55,
            )

            # Update Python lists to reflect new logical order
            box_list[i], box_list[j] = box_list[j], box_list[i]
            label_list[i], label_list[j] = label_list[j], label_list[i]
            current_values[i], current_values[j] = current_values[j], current_values[i]

            # Reset stroke to GREY_B (default STRUCT color)
            self.play(
                box_list[i].animate.set_stroke(color=GREY_B, width=2.5),
                box_list[j].animate.set_stroke(color=GREY_B, width=2.5),
                run_time=0.2,
            )

            return 0.25 + 0.25 + 0.3 + 0.55 + 0.2  # = 1.55s per swap

        # CUE 1 — 7.0s: Run one full pass of bubble sort
        anim_time = 0.3  # scan_rect ShowCreation above
        for i in range(len(values) - 1):
            if current_values[i] > current_values[i + 1]:
                anim_time += animate_swap(i, i + 1)
            else:
                # just scan — show TEAL_A highlight moving forward
                scan_rect.become(SurroundingRectangle(box_list[i], color=TEAL_A, buff=0.06, stroke_width=2.5))
                self.play(ShowCreation(scan_rect), run_time=0.2)
                anim_time += 0.2
        self.wait(max(0.01, 7.0 - anim_time))

        # CUE 2 — 3.0s: Mark the largest (90) as sorted with GREEN (SUCCESS)
        # After one full pass, the largest element is at the last position
        self.play(FadeOut(scan_rect), run_time=0.2)
        self.play(
            box_list[-1].animate.set_fill(GREEN, opacity=0.35).set_stroke(color=GREEN, width=2.5),
            run_time=0.5,
        )
        sorted_label = Text("Sorted", font_size=22, color=GREEN).next_to(box_list[-1], DOWN, buff=0.25)
        self.play(FadeIn(sorted_label), run_time=0.4)
        self.wait(3.0 - 0.2 - 0.5 - 0.4)  # 1.9s ✓

        # CUE 3 — 1.5s: Final FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.7)  # 0.8 + 0.7 = 1.5 ✓
