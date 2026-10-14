"""The loop-timing law must open the Director's Cue timing section (#59).

Director under-counting of loop time is the root cause of empty and frozen cue
clips, so the rule has to be the first thing under the heading and carry both
the RIGHT (accumulate, then subtract) and the WRONG (subtract one iteration)
example.
"""

from pathlib import Path

import manimgen

_PROMPT = (
    Path(manimgen.__file__).parent / "generator" / "prompts" / "director_system.md"
)


def _cue_timing_section() -> str:
    text = _PROMPT.read_text(encoding="utf-8")
    start = text.index("\n## Cue timing\n")
    end = text.index("\n## ", start + 1)
    return text[start:end].split("\n", 2)[2]


def test_loop_rule_is_first_in_cue_timing_section():
    body = _cue_timing_section().lstrip()
    first_block = body.split("\n\n", 1)[0]
    assert "loop timing" in first_block.lower()


def test_loop_rule_has_right_and_wrong_examples():
    body = _cue_timing_section()
    right = body.index("anim_time += ")
    subtract = body.index("D - anim_time")
    wrong = body.index("WRONG")
    one_iteration = body.index("self.wait(D - 0.2)")
    assert right < subtract
    assert wrong < one_iteration
    # Both examples come before the first non-loop example.
    assert max(subtract, one_iteration) < body.index("# CUE 0")
