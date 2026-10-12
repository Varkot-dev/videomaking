from manimlib import *


class ArraySwapScene(Scene):
    """
    techniques: array_swap
    Demonstrates animating element swaps (bubble sort, selection sort, insertion sort).

    CRITICAL PATTERN — VGroup does NOT support item assignment.
    Swapping indices directly on a VGroup crashes with TypeError at runtime.

    CORRECT APPROACH — use a parallel Python list for index tracking:
        box_list = list(boxes)                     # mutable Python list
        box_list[i], box_list[j] = box_list[j], box_list[i]  # swap the list

    The VGroup is used ONLY for layout (initial arrangement).
    After any swap, use the Python list for position lookups — never index into VGroup.

    To animate the swap: move the mobjects to each other's current positions.
    No VGroup rebuild needed — the mobjects are already on screen.
    """

    def construct(self):
        values = [64, 34, 25, 12, 22, 11, 90]

        # Build boxes and labels as VGroup (for initial layout only)
        boxes = VGroup(*[
            Square(side_length=0.85, fill_color="#2a2a2a", fill_opacity=1,
                   stroke_width=2, color=GREY_B)
            for _ in values
        ]).arrange(RIGHT, buff=0.14).center()

        labels = VGroup(*[
            Text(str(v), font_size=26, color=WHITE).move_to(boxes[i])
            for i, v in enumerate(values)
        ])

        title = Text("Bubble Sort — Swap Animation", font_size=34, color=WHITE).to_edge(UP, buff=0.8)

        # CUE 0 — 3.0s: Reveal the array
        self.play(Write(title), run_time=0.6)
        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.08), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(l) for l in labels], lag_ratio=0.08), run_time=0.6)
        self.wait(1.0)  # 0.6 + 0.8 + 0.6 + 1.0 = 3.0 ✓

        # CRITICAL: build parallel Python lists for index tracking after swaps
        # box_list[i] = the Square currently at logical position i
        # label_list[i] = the Text label currently at logical position i
        box_list = list(boxes)
        label_list = list(labels)

        swap_counter = [0]

        counter_text = Text("Swaps: 0", font_size=26, color=TEAL_C).to_edge(DOWN, buff=0.6)
        self.play(FadeIn(counter_text), run_time=0.4)

        def animate_swap(i, j):
            """Animate swapping positions i and j. Returns time consumed."""
            box_i, box_j = box_list[i], box_list[j]
            lbl_i, lbl_j = label_list[i], label_list[j]

            # Highlight the two elements being compared
            self.play(
                box_i.animate.set_color(YELLOW),
                box_j.animate.set_color(YELLOW),
                run_time=0.3,
            )

            # Record current positions before moving
            pos_i = box_i.get_center()
            pos_j = box_j.get_center()

            # Animate both boxes AND their labels to each other's positions
            self.play(
                box_i.animate.move_to(pos_j),
                box_j.animate.move_to(pos_i),
                lbl_i.animate.move_to(pos_j),
                lbl_j.animate.move_to(pos_i),
                run_time=0.55,
            )

            # Update the Python lists to reflect the new logical order
            box_list[i], box_list[j] = box_list[j], box_list[i]
            label_list[i], label_list[j] = label_list[j], label_list[i]

            # Reset highlight
            self.play(
                box_list[i].animate.set_color(GREY_B),
                box_list[j].animate.set_color(GREY_B),
                run_time=0.25,
            )

            # Update swap counter via FadeTransform
            swap_counter[0] += 1
            new_counter = Text(f"Swaps: {swap_counter[0]}", font_size=26, color=TEAL_C).to_edge(DOWN, buff=0.6)
            self.play(FadeTransform(counter_text, new_counter), run_time=0.3)
            counter_text.become(new_counter)

            return 0.3 + 0.55 + 0.25 + 0.3  # = 1.4s per swap

        # CUE 1 — 6.0s: Run one pass of bubble sort (first 3 comparisons)
        # Values: [64, 34, 25, 12, 22, 11, 90]
        # Compare positions 0,1 → swap 64,34  → [34, 64, 25, 12, 22, 11, 90]
        # Compare positions 1,2 → swap 64,25  → [34, 25, 64, 12, 22, 11, 90]
        # Compare positions 2,3 → swap 64,12  → [34, 25, 12, 64, 22, 11, 90]
        anim_time = 0.4  # counter FadeIn above
        for i in range(3):
            anim_time += animate_swap(i, i + 1)
        self.wait(max(0.01, 6.0 - anim_time))

        # CUE 2 — 3.5s: Flash the largest value (90 is already at end, unmoved)
        highlight = SurroundingRectangle(box_list[-1], color=GREEN, buff=0.08)
        self.play(ShowCreation(highlight), run_time=0.4)
        sorted_label = Text("In place", font_size=22, color=GREEN).next_to(highlight, DOWN, buff=0.2)
        self.play(FadeIn(sorted_label), run_time=0.4)
        self.wait(3.5 - 0.4 - 0.4)  # 2.7s ✓

        # CUE 3 — 2.0s: Final FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(1.2)  # 0.8 + 1.2 = 2.0 ✓
