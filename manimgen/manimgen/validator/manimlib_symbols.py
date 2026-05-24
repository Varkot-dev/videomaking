"""Allowlist membership check against the pinned ManimGL symbol table (#30).

codeguard is historically an *unbounded denylist*: it only catches idioms that
someone has already taught it are wrong. Anything the Director hallucinates that
is not yet a known-bad pattern sails straight through to a render crash. An
*allowlist* is the complete inverse: "does this name actually exist in the
``manimlib`` we pinned?" — but flipping that on as a hard gate is dangerous
(false positives would block good renders, and ``manimlib`` is not even
importable in CI). So this module is wired in **report-only / shadow mode**:
it logs what it *would* flag and never blocks, degrades, or alters a render.

Everything here is fail-open. If ``manimlib`` cannot be imported (CI, no GL
install) the symbol set is ``None`` and the shadow check returns nothing.
"""

import ast
import builtins
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Names that legitimately appear in generated scenes but are NOT manimlib
# symbols: Python builtins, the module-level numpy alias, and self/cls.
_ALWAYS_ALLOWED: frozenset[str] = frozenset(
    set(dir(builtins))
    | {
        "np",  # `import numpy as np` is conventional in generated scenes
        "self",
        "cls",
        "math",
        "random",
        "it",  # `import itertools as it`
        "DEGREES",  # exported by manimlib but defensive; harmless if duplicated
    }
)


@lru_cache(maxsize=1)
def load_manimlib_symbols() -> frozenset[str] | None:
    """Return the set of public names exported by the pinned ``manimlib``.

    Returns ``None`` (fail-open) when ``manimlib`` cannot be imported — the
    caller must treat ``None`` as "allowlist unavailable, do nothing". Cached
    so the (potentially heavy) import happens at most once per process.
    """
    try:
        import manimlib  # noqa: PLC0415  (deferred on purpose — heavy/optional)
    except Exception as exc:  # ImportError, or GL/display init failure
        logger.info(
            "[codeguard][allowlist-shadow] manimlib unavailable (%s) — "
            "allowlist check disabled this run",
            exc,
        )
        return None

    return frozenset(name for name in dir(manimlib) if not name.startswith("_"))


def _locally_bound_names(tree: ast.AST) -> set[str]:
    """Collect names defined within the module: assignments, imports, defs, args.

    These are never "unknown manimlib symbols" — they are the scene's own
    helpers, loop variables, and bindings. Excluding them keeps the shadow
    report focused on genuinely undefined references.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.comprehension):
            for tgt in ast.walk(node.target):
                if isinstance(tgt, ast.Name):
                    bound.add(tgt.id)
    return bound


def shadow_check_allowlist(code: str) -> list[str]:
    """Return manimlib-callable names referenced in ``code`` that are unknown.

    "Unknown" = a bare name used as a call target (``Foo(...)``) that is neither
    a Python builtin, a locally bound name, nor a member of the pinned manimlib
    symbol table. This is **report-only**: the caller logs the result and does
    nothing else.

    Always fail-open: returns ``[]`` when the symbol table is unavailable or the
    code cannot be parsed. Never raises.
    """
    symbols = load_manimlib_symbols()
    if symbols is None:
        return []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # A syntax error is the denylist/precheck's job to report — not ours.
        return []

    bound = _locally_bound_names(tree)

    flagged: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        # Only consider call targets that are bare names: `Foo(...)`. Attribute
        # calls (`obj.method(...)`) and kwargs are out of scope — an allowlist
        # over the flat symbol table cannot judge attribute membership.
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name in seen:
            continue
        if name in _ALWAYS_ALLOWED or name in bound or name in symbols:
            continue
        seen.add(name)
        flagged.append(name)

    return flagged
