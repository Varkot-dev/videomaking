"""Pre-execution AST gate for LLM-authored ManimGL scene code (#27).

A generated scene file is handed straight to ``manimgl <file> <Class>``, which
imports the module — i.e. it *executes every top-level statement* before the
Scene class is ever instantiated. So a single line that shells out at module
scope runs as soon as manimgl imports the file. codeguard catches a fixed list
of *known-bad* patterns, but it is a denylist over a string and cannot see
"any side-effecting top-level statement". This gate is the allowlist
complement: it parses the code and asserts the module body contains *only* the
shapes a real Director scene legitimately uses.

Allowed top-level statements (everything the 32 verified example scenes use,
and nothing else):
  - a module docstring
  - ``from manimlib import *`` / ``import manimlib``
  - ``import numpy`` / ``import numpy as np`` / ``import math``
  - exactly one ``class ...(...)`` definition (the Scene)
  - ``from __future__ import ...`` (harmless, occasionally emitted)

Anything else — a bare ``Call`` that shells out, ``import os`` / ``subprocess``
/ ``socket``, a top-level assignment that runs arbitrary code, ``exec``/``eval``,
a second class, dunder writes — is reported as a finding.

Wiring (see ``runner.run_scene``): this gate is currently **warning-only**. It
logs every finding loudly but does NOT block the render, because hard-breaking
would regress any scene whose Director happened to emit an extra (benign)
top-level statement, and the blast radius of a false negative here is a failed
render, not RCE — codeguard's banned-pattern denylist still blocks the known
exec/eval/shell primitives outright. Once Director output is constrained
enough that findings are reliably malicious, flip ``hard_block=True`` at the
call site. See the TODO in ``runner.run_scene``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Module names that may be imported at scene top level. Kept deliberately tiny:
# the verified example corpus only ever imports manimlib + numpy, and the
# generator itself only needs math. Adding networking/process/filesystem
# modules here would defeat the gate.
_ALLOWED_IMPORT_MODULES = frozenset(
    {
        "manimlib",
        "numpy",
        "math",
        "__future__",
    }
)


@dataclass(frozen=True)
class GateResult:
    """Outcome of inspecting one scene module's top-level body.

    Attributes:
        ok:       True when no disallowed top-level statement was found.
        findings: Human-readable description of each rejected statement,
                  prefixed with its 1-based line number.
        class_count: Number of top-level ClassDef nodes seen (a valid scene
                  has exactly one).
    """

    ok: bool
    findings: list[str] = field(default_factory=list)
    class_count: int = 0


def _describe(node: ast.stmt) -> str:
    """Short, line-anchored description of a rejected top-level statement."""
    lineno = getattr(node, "lineno", "?")
    kind = type(node).__name__
    return f"line {lineno}: disallowed top-level {kind}"


def _import_is_allowed(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.ImportFrom):
        # `from manimlib import *`, `from __future__ import ...`
        return (node.module or "").split(".")[0] in _ALLOWED_IMPORT_MODULES
    # `import a, b` — every imported root module must be allowed.
    return all(
        alias.name.split(".")[0] in _ALLOWED_IMPORT_MODULES for alias in node.names
    )


def inspect_scene_code(code: str) -> GateResult:
    """Inspect generated scene source for disallowed top-level statements.

    Does no I/O and never executes the code — it only parses it. A syntax
    error is reported as a finding (and ``ok=False``) rather than raising, so
    callers in the render path never crash on malformed LLM output (codeguard
    reports syntax errors separately for the render-blocking path).

    Args:
        code: The full scene module source.

    Returns:
        A :class:`GateResult`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return GateResult(
            ok=False,
            findings=[f"line {exc.lineno}: SyntaxError: {exc.msg}"],
            class_count=0,
        )

    findings: list[str] = []
    class_count = 0

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_count += 1
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _import_is_allowed(node):
                continue
            names = (
                node.module
                if isinstance(node, ast.ImportFrom)
                else ", ".join(a.name for a in node.names)
            )
            findings.append(
                f"line {getattr(node, 'lineno', '?')}: disallowed top-level "
                f"import of {names!r} (only {sorted(_ALLOWED_IMPORT_MODULES)} allowed)"
            )
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # Module docstring (or any bare string/number literal) — inert.
            continue
        findings.append(_describe(node))

    if class_count == 0:
        findings.append("no top-level Scene class found")
    elif class_count > 1:
        findings.append(f"{class_count} top-level classes found (expected exactly 1)")

    return GateResult(ok=not findings, findings=findings, class_count=class_count)


def inspect_scene_file(scene_path: str) -> GateResult:
    """Read a scene file and run :func:`inspect_scene_code` over its contents."""
    with open(scene_path) as f:
        return inspect_scene_code(f.read())
