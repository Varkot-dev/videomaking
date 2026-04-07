"""
TextTemplate — generates ManimGL scenes for text/bullet point displays.
Beat types: title_only, bullets, highlight, dim_others, transition.
"""
from manimgen.templates.base import TemplateScene


class TextTemplate(TemplateScene):

    def scene_setup(self) -> list[str]:
        return ["_bullets = None", "_highlight_rect = None", ""]

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "title_only":
            return self._title_only(beat)
        if t == "bullets":
            return self._bullets(beat)
        if t == "highlight":
            return self._highlight(beat)
        if t == "dim_others":
            return self._dim_others(beat)
        if t == "transition":
            return self._transition(beat)
        return []

    def _title_only(self, beat: dict) -> list[str]:
        title_text = beat.get("title", self.title)
        subtitle_text = beat.get("subtitle", "")
        duration = beat.get("duration", 2.0)
        escaped_title = title_text.replace('"', '\\"')
        lines = [
            f'_slide_title = Text("{escaped_title}", font_size=64, color=BLUE)',
            "_slide_title.to_edge(UP, buff=0.8)",
            "self.play(Write(_slide_title))",
        ]
        if subtitle_text:
            escaped_sub = subtitle_text.replace('"', '\\"')
            lines += [
                f'_subtitle = Text("{escaped_sub}", font_size=30, color=GREY_A)',
                "_subtitle.next_to(_slide_title, DOWN, buff=0.4)",
                "self.play(FadeIn(_subtitle))",
            ]
        lines += [f"self.wait({duration})"]
        return lines

    def _bullets(self, beat: dict) -> list[str]:
        items = beat.get("items", [])
        colors = beat.get("colors", [])
        duration = beat.get("duration", 2.5)

        color_list = colors + ["WHITE"] * (len(items) - len(colors))
        lines = ["_bullet_texts = ["]
        for i, item in enumerate(items):
            color = color_list[i] if i < len(color_list) else "WHITE"
            escaped = item.replace('"', '\\"')
            lines.append(f'    Text("{escaped}", font_size=28, color={color}),')
        lines += [
            "]",
            "_bullets = VGroup(*_bullet_texts).arrange(DOWN, aligned_edge=LEFT, buff=0.35)",
            "_bullets.move_to(ORIGIN)",
            "self.play(LaggedStart(*[FadeIn(b) for b in _bullets], lag_ratio=0.4))",
            f"self.wait({duration})",
        ]
        return lines

    def _highlight(self, beat: dict) -> list[str]:
        index = beat.get("index", 0)
        color = beat.get("color", "YELLOW")
        return [
            f"_highlight_rect = SurroundingRectangle(_bullets[{index}], color={color}, buff=0.1)",
            "self.play(ShowCreation(_highlight_rect))",
            "self.wait(1.5)",
        ]

    def _dim_others(self, beat: dict) -> list[str]:
        keep = beat.get("keep_index", 0)
        return [
            f"self.play(*[b.animate.set_opacity(0.3) for i, b in enumerate(_bullets) if i != {keep}])",
            "self.wait(1.5)",
            f"self.play(*[b.animate.set_opacity(1.0) for b in _bullets])",
        ]

    def _transition(self, beat: dict) -> list[str]:
        self._last_beat_cleared_scene = True
        return [
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)",
            "_bullets = None",
            "_highlight_rect = None",
            "self.wait(0.2)",
        ]
