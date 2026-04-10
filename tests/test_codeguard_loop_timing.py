"""
Tests for codeguard._check_loop_timing_smells()

Zero LLM calls, zero subprocess calls.
"""

import pytest
from manimgen.validator.codeguard import _check_loop_timing_smells


class TestCheckLoopTimingSmells:

    def test_detects_missing_accumulator_after_for_loop(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        boxes = [Square() for _ in range(5)]
        for i in range(4):
            self.play(boxes[i].animate.shift(RIGHT), run_time=0.3)
        self.wait(2.8)
"""
        warnings = _check_loop_timing_smells(code)
        assert len(warnings) == 1
        assert "anim_time" in warnings[0]
        assert "Loop timing" in warnings[0]

    def test_detects_missing_accumulator_after_while_loop(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        i = 0
        while i < 5:
            self.play(Dot().animate.shift(UP), run_time=0.5)
            i += 1
        self.wait(3.0)
"""
        warnings = _check_loop_timing_smells(code)
        assert len(warnings) == 1
        assert "Loop timing" in warnings[0]

    def test_no_warning_when_accumulator_present(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        anim_time = 0.0
        for i in range(4):
            self.play(Dot().animate.shift(RIGHT), run_time=0.3)
            anim_time += 0.3
        self.wait(max(0.01, 4.0 - anim_time))
"""
        warnings = _check_loop_timing_smells(code)
        assert warnings == []

    def test_no_warning_when_loop_has_no_run_time(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        for i in range(4):
            x = i * 2
        self.wait(2.0)
"""
        warnings = _check_loop_timing_smells(code)
        assert warnings == []

    def test_no_warning_when_no_self_wait_follows(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        for i in range(4):
            self.play(Dot().animate.shift(RIGHT), run_time=0.3)
        self.play(FadeOut(Dot()))
"""
        warnings = _check_loop_timing_smells(code)
        assert warnings == []

    def test_no_warning_on_clean_scene(self):
        code = """\
from manimlib import *

class CleanScene(Scene):
    def construct(self):
        title = Text("Binary Search")
        self.play(Write(title), run_time=1.5)
        self.wait(2.5)
        self.play(FadeOut(title), run_time=0.8)
"""
        warnings = _check_loop_timing_smells(code)
        assert warnings == []

    def test_multiple_loops_both_missing_accumulator(self):
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        for i in range(3):
            self.play(Dot().animate.shift(UP), run_time=0.4)
        self.wait(2.8)
        for j in range(2):
            self.play(Square().animate.shift(DOWN), run_time=0.5)
        self.wait(3.0)
"""
        warnings = _check_loop_timing_smells(code)
        assert len(warnings) == 2

    def test_any_accumulator_var_name_suppresses_warning(self):
        """Any variable name used with += in the loop and referenced in wait() is accepted."""
        for varname in ("total_time", "elapsed", "dur", "t", "accumulated"):
            code = f"""\
from manimlib import *

class Foo(Scene):
    def construct(self):
        {varname} = 0.0
        for i in range(5):
            self.play(Dot().animate.shift(RIGHT), run_time=0.2)
            {varname} += 0.2
        self.wait(max(0.01, 5.0 - {varname}))
"""
            warnings = _check_loop_timing_smells(code)
            assert warnings == [], f"False warning for accumulator named '{varname}': {warnings}"

    def test_no_warning_when_self_play_between_loop_and_wait(self):
        """Intervening self.play() after loop means timing is handled — no warning."""
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        for i in range(4):
            self.play(Dot().animate.shift(RIGHT), run_time=0.3)
        self.play(FadeIn(Text("done")), run_time=0.5)
        self.wait(2.0)
"""
        warnings = _check_loop_timing_smells(code)
        assert warnings == []

    def test_warns_when_accumulator_exists_but_not_referenced_in_wait(self):
        """Loop has += accumulator but it's not used in the wait() — timing still wrong."""
        code = """\
from manimlib import *

class Foo(Scene):
    def construct(self):
        elapsed = 0.0
        for i in range(4):
            self.play(Dot().animate.shift(RIGHT), run_time=0.3)
            elapsed += 0.3
        self.wait(2.8)
"""
        warnings = _check_loop_timing_smells(code)
        assert len(warnings) == 1
        assert "Loop timing" in warnings[0]
