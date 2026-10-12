"""Unknown-symbol check against the pinned ManimGL symbol table (#30, #60).

codeguard is historically an *unbounded denylist*: it only catches idioms that
someone has already taught it are wrong. Anything the Director hallucinates that
is not yet a known-bad pattern sails straight through to a render crash (a real
run wrote ``Star(...)``, which ManimGL 1.7.2 does not export, and paid for a full
render plus an LLM repair call before finding out).

This module answers "does this bare name exist?" without rendering:

* The symbol table is the set of names ``from manimlib import *`` binds. It is
  shipped as data (``manimlib_symbols.json``, written by
  ``scripts/gen_manimlib_symbols.py``) because importing ``manimlib`` needs a
  display and fails headless. The live import is preferred when it works, the
  shipped table is the fallback, so the check never silently disables itself.
* ``find_unknown_names`` is scope-aware and conservative: only bare ``Name``
  loads count, and any name bound anywhere in the file, any builtin, and any
  allowed module alias is fine. Strings, comments and attribute names never
  count because they are not ``Name`` loads.
* Enforcement lives in codeguard (it becomes a precheck error). Set
  ``MANIMGEN_UNKNOWN_SYMBOLS=report`` to fall back to report-only logging.

Everything here is fail-open: no table or unparseable code yields no findings.
"""

import ast
import builtins
import difflib
import json
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

SYMBOLS_JSON = Path(__file__).with_name("manimlib_symbols.json")

# Env var: "report" downgrades enforcement to log-only (the old shadow mode).
ENFORCE_ENV = "MANIMGEN_UNKNOWN_SYMBOLS"

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


def enforcement_enabled() -> bool:
    """False only when the kill switch asks for report-only mode."""
    return os.environ.get(ENFORCE_ENV, "").strip().lower() not in {
        "report",
        "off",
        "0",
        "false",
    }


def load_shipped_symbols() -> frozenset[str] | None:
    """Read the table shipped next to this module; None if missing or corrupt."""
    try:
        data = json.loads(SYMBOLS_JSON.read_text(encoding="utf-8"))
        return frozenset(str(n) for n in data["names"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def live_star_import_names() -> frozenset[str] | None:
    """Names ``from manimlib import *`` binds in this environment, or None.

    None when manimlib cannot be imported (no display, no GL, not installed).
    """
    # manimlib parses sys.argv at import time (manimlib/config.py parse_cli).
    # Under a test runner / any host process, sys.argv carries flags manimlib's
    # argparse rejects, raising SystemExit (NOT an Exception subclass). Blank
    # argv across the import so introspection can't consume the host's CLI.
    saved_argv = sys.argv
    try:
        sys.argv = [saved_argv[0] if saved_argv else "manimgen"]
        ns: dict = {}
        exec("from manimlib import *", ns)  # noqa: S102  (constant source)
    except (Exception, SystemExit) as exc:  # ImportError, GL init, argv parse
        logger.info("[codeguard][allowlist] manimlib not importable (%s)", exc)
        return None
    finally:
        sys.argv = saved_argv
    return frozenset(n for n in ns if not n.startswith("_"))


@lru_cache(maxsize=1)
def load_manimlib_symbols() -> frozenset[str] | None:
    """The ManimGL symbol table: live import if possible, else the shipped JSON.

    Cached so the (potentially heavy) import happens at most once per process.
    ``None`` only if both sources are unavailable (the caller then does nothing).
    """
    return live_star_import_names() or load_shipped_symbols()


def _locally_bound_names(tree: ast.AST) -> set[str]:
    """Every name the module binds anywhere (scope-blind on purpose).

    Being scope-blind can only hide a finding, never invent one, which keeps the
    enforced check free of false positives.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
    return bound


def _has_foreign_star_import(tree: ast.AST) -> bool:
    """`from other import *` binds unknowable names: do not judge such files."""
    return any(
        isinstance(n, ast.ImportFrom)
        and any(a.name == "*" for a in n.names)
        and (n.module or "").split(".")[0] != "manimlib"
        for n in ast.walk(tree)
    )


def find_unknown_names(code: str) -> list[tuple[str, int]]:
    """Bare names loaded in ``code`` that exist nowhere: ``[(name, first_line)]``.

    A name is fine when it is bound in the file, a Python builtin, a module-level
    dunder, or in the ManimGL table. Only ``Name`` loads are examined, so
    attribute names, keyword names, strings and comments can never be flagged.
    Fail-open: ``[]`` when the table is unavailable or the code does not parse.
    """
    symbols = load_manimlib_symbols()
    if symbols is None:
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        # A syntax error is the denylist/precheck's job to report, not ours.
        return []
    if _has_foreign_star_import(tree):
        return []

    bound = _locally_bound_names(tree)
    first_line: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        name = node.id
        if (
            name in _ALWAYS_ALLOWED
            or name in bound
            or name in symbols
            or (name.startswith("__") and name.endswith("__"))
        ):
            continue
        line = getattr(node, "lineno", 0)
        if name not in first_line or line < first_line[name]:
            first_line[name] = line
    return sorted(first_line.items(), key=lambda kv: (kv[1], kv[0]))


def shadow_check_allowlist(code: str) -> list[str]:
    """Names ``find_unknown_names`` flags, without line numbers (never raises)."""
    return [name for name, _ in find_unknown_names(code)]


def suggest_alternatives(name: str, n: int = 4) -> list[str]:
    """Closest real ManimGL names to ``name`` (difflib, CamelCase-aware)."""
    symbols = load_manimlib_symbols() or frozenset()
    camel = name[:1].isupper()
    lowered = {s.lower(): s for s in sorted(symbols) if s[:1].isupper() == camel}
    hits = difflib.get_close_matches(name.lower(), list(lowered), n=n, cutoff=0.5)
    return [lowered[h] for h in hits]


def format_unknown_symbol_errors(findings: list[tuple[str, int]]) -> list[str]:
    """One precheck error line per unknown name, with real alternatives."""
    errors = []
    for name, line in findings:
        alts = suggest_alternatives(name)
        msg = (
            f"Unknown name `{name}` (line {line}): it is not defined in the scene "
            f"and is not exported by ManimGL 1.7.2 (`from manimlib import *`), so "
            f"it would raise NameError at render time."
        )
        if alts:
            msg += " Closest real ManimGL names: " + ", ".join(alts) + "."
        if name[:1].isupper() and not any(
            difflib.SequenceMatcher(None, name.lower(), a.lower()).ratio() >= 0.7
            for a in alts
        ):
            msg += (
                " If you need a shape ManimGL lacks, build it from "
                "Polygon(*points) or RegularPolygon(n) (or define a helper)."
            )
        errors.append(msg)
    return errors
