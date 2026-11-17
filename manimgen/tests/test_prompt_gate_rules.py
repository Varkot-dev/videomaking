"""The Director and retry prompts must describe the safety gate truthfully.

The gate rejects some harmless-looking code (a leading-underscore helper, a
variable named ``log``). Each rejection costs an LLM retry, so the prompts tell
the model the rules up front. These tests keep the prompt text and the gate in
step: if the gate changes, the prompt must change with it.
"""

import os
import re

import pytest

import manimgen
from manimgen.validator import scene_ast_gate as gate

pytestmark = pytest.mark.unit

_PKG = os.path.dirname(manimgen.__file__)
_DIRECTOR = os.path.join(_PKG, "generator", "prompts", "director_system.md")
_RETRY = os.path.join(_PKG, "validator", "prompts", "retry_system.md")


def _section(path: str, heading: str) -> str:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start = text.index(heading)
    end = text.index("\n## ", start + len(heading))
    return text[start:end]


def _scene(body: str) -> str:
    lines = "\n".join("        " + ln for ln in body.splitlines())
    return f"from manimlib import *\n\n\nclass S(Scene):\n    def construct(self):\n{lines}\n"


def test_director_lists_exactly_the_importable_modules():
    section = _section(_DIRECTOR, "## Safety gate")
    bullet = section[section.index("**Imports:**") : section.index("No `os`")]
    listed = set(re.findall(r"`(\w+)`", bullet))
    assert listed == set(gate._ALLOWED_IMPORT_MODULES) - {"__future__"}


def test_every_reserved_word_the_prompts_name_is_really_rejected():
    for path, heading in ((_DIRECTOR, "## Safety gate"), (_RETRY, "## Safety gate")):
        section = _section(path, heading)
        for word in ("log", "input", "signal", "types", "platform", "op", "Path"):
            assert re.search(rf"[`']{word}[`']", section), (path, word)
            assert word in gate._DENIED_NAMES
            assert not gate.inspect_scene_code(_scene(f"{word} = 1\nself.wait(1)")).ok


def test_the_patterns_the_prompt_recommends_pass_the_gate():
    good = (
        "import math\n"
        "log_value = np.log(2) + math.log(3)\n"
        "wave_signal = [1, 2]\n"
        "self.add(self.make_box(1))\n"
        "self.wait(1)"
    )
    code = _scene(good) + "\n    def make_box(self, n):\n        return Square()\n"
    result = gate.inspect_scene_code(code)
    assert result.ok, result


def test_underscore_helper_is_rejected_as_the_prompt_says():
    code = _scene("self.add(self._make_box(1))\nself.wait(1)")
    code += "\n    def _make_box(self, n):\n        return Square()\n"
    assert not gate.inspect_scene_code(code).ok
