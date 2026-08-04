"""Tests for the pre-execution AST gate, and for what Codeguard does *not* do.

Generated scene files are handed to ``manimgl <file> <Class>``, which imports
the module and therefore executes every top-level statement. These tests pin
which component actually inspects that code.

They exist because an earlier docstring in ``scene_ast_gate`` claimed Codeguard
"blocks the known exec/eval/shell primitives outright", and that claim was used
to justify leaving this gate warning-only. The claim was false: the two modules
were each deferring to a check the other did not perform. A test that asserts
the real behaviour is harder to drift than a comment.
"""
from __future__ import annotations

import pytest

from manimgen.validator.codeguard import _BANNED_PATTERNS
from manimgen.validator.scene_ast_gate import inspect_scene_code

# Attack payloads, held as inert strings. Nothing here is executed: the gate
# parses them with ast.parse and reports findings, and these tests assert it
# does. They must contain the real primitive names to be meaningful — a test
# for exec-detection that avoids writing "exec" tests nothing.
DANGEROUS_SNIPPETS = {
    "exec": 'exec("import os")',
    "eval": 'eval("__import__(\'os\').system(\'id\')")',
    "dunder_import": '__import__("os").system("id")',
    "os_system": 'import os\nos.system("id")',
    "subprocess": 'import subprocess\nsubprocess.run(["id"])',
    "socket": 'import socket\nsocket.socket()',
    "file_write": 'open("/tmp/x", "w").write("x")',
}


def _scene(body: str) -> str:
    """Wraps a snippet at module scope, where manimgl would execute it on import."""
    return f'from manimlib import *\n{body}\n\nclass S(Scene):\n    def construct(self):\n        pass\n'


class TestCodeguardIsNotASecurityBoundary:
    """Pins the fact that Codeguard's denylist is API-compat only."""

    def test_no_banned_pattern_targets_a_dangerous_primitive(self):
        dangerous = ("exec", "eval", "__import__", "os", "subprocess", "socket", "open(")
        matching = [p for p, _ in _BANNED_PATTERNS if any(d in p for d in dangerous)]

        assert matching == [], (
            "A banned pattern now targets a dangerous primitive. That may be a "
            "genuine improvement — if so, update the warning in "
            "scene_ast_gate's module docstring, which currently states that "
            "Codeguard blocks none of these."
        )

    def test_banned_patterns_are_all_api_compatibility_rules(self):
        # 24 at the time of writing. The exact count may change; the point is
        # that this list governs ManimGL API usage, not execution safety.
        assert len(_BANNED_PATTERNS) > 0
        assert all(isinstance(p, str) and isinstance(m, str) for p, m in _BANNED_PATTERNS)


class TestAstGateFlagsDangerousTopLevelCode:
    """The AST gate is the only component that sees these constructs."""

    @pytest.mark.parametrize("name,snippet", sorted(DANGEROUS_SNIPPETS.items()))
    def test_dangerous_top_level_statement_is_reported(self, name, snippet):
        result = inspect_scene_code(_scene(snippet))

        assert not result.ok, f"{name} at module scope produced no finding"
        assert result.findings, f"{name} was marked not-ok but reported no findings"

    def test_a_legitimate_scene_passes_cleanly(self):
        code = (
            '"""A docstring."""\n'
            "from manimlib import *\n"
            "import numpy as np\n"
            "\n"
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        self.play(FadeIn(Dot()))\n"
        )
        result = inspect_scene_code(code)

        assert result.ok, f"legitimate scene was flagged: {result.findings}"

    def test_syntax_error_is_reported_not_raised(self):
        result = inspect_scene_code("class S(Scene):\n    def construct(self)\n")

        assert not result.ok
        assert result.findings
