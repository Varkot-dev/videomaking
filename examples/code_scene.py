from manimlib import *


class CodeScene(Scene):
    """
    Pattern: Code/pseudocode display — steps appearing line by line,
    with highlighting to draw attention to specific lines.
    Covers: Text, VGroup, FadeIn, SurroundingRectangle, LaggedStart,
            arrange, set_color, line-by-line reveal.
    """

    def construct(self):
        # --- Title ---
        title = Text("Binary Search Algorithm", font_size=44, color=BLUE)
        title.to_edge(UP, buff=0.6)
        self.play(Write(title))
        self.wait(0.5)

        # --- Pseudocode lines ---
        lines_text = [
            ("def binary_search(arr, target):", WHITE),
            ("    low, high = 0, len(arr) - 1", GREY_A),
            ("    while low <= high:", GREY_A),
            ("        mid = (low + high) // 2", GREY_A),
            ("        if arr[mid] == target:", GREY_A),
            ("            return mid", GREEN),
            ("        elif arr[mid] < target:", GREY_A),
            ("            low = mid + 1", YELLOW),
            ("        else:", GREY_A),
            ("            high = mid - 1", YELLOW),
            ("    return -1", RED),
        ]

        # Build text mobjects
        code_lines = VGroup(*[
            Text(text, font="Courier New", font_size=22, color=color)
            for text, color in lines_text
        ]).arrange(DOWN, aligned_edge=LEFT, buff=0.18)
        code_lines.move_to(ORIGIN + DOWN * 0.3)

        # Reveal lines one by one
        self.play(LaggedStart(
            *[FadeIn(line) for line in code_lines],
            lag_ratio=0.15,
        ), run_time=3)
        self.wait(1)

        # --- Highlight the key line ---
        key_line = code_lines[3]  # mid = (low + high) // 2
        highlight = SurroundingRectangle(key_line, color=YELLOW, buff=0.1)
        annotation = Text("← The core idea", font_size=22, color=YELLOW)
        annotation.next_to(highlight, RIGHT, buff=0.2)

        self.play(ShowCreation(highlight), Write(annotation))
        self.wait(2)

        # --- Dim everything except the highlight ---
        self.play(
            *[line.animate.set_opacity(0.3) for i, line in enumerate(code_lines) if i != 3],
            FadeOut(annotation),
        )
        self.wait(1.5)

        # --- Restore and exit ---
        self.play(*[line.animate.set_opacity(1.0) for line in code_lines])
        self.play(FadeOut(code_lines), FadeOut(highlight), FadeOut(title))
        self.wait(0.5)
