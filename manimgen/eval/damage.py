"""Must-not-break tier: how much does Codeguard damage known-good scenes?

Every file in ``examples/`` is hand-verified ManimGL code that renders. Codeguard's
static repair path should therefore leave it alone, apart from harmless edits. This
module runs that path over each example and measures what changed, with no network
and no LLM.

Per example, the repaired source is compared with the original:

  - ``compiles``            the repaired source still parses
  - ``defs_kept``           every class / def name is still there (as a multiset)
  - ``play_wait_kept``      the self.play and self.wait call counts are unchanged
  - ``new_undefined``       names loaded but never bound in the repaired file that
                            were not already unbound in the original and are not in
                            the known-good vocabulary (every unbound name used by
                            any example, i.e. the manimlib names the star import
                            supplies). Catches a rule that rewrites a valid name
                            into one that does not exist, without needing manimlib
                            installed.
  - ``animations_dropped``  expressions passed to self.play that existed before
                            and are gone after (an edited animation counts too)
  - ``stmts_removed``       statements present before and absent after (shallow
                            AST comparison; compound statements compare headers)
  - ``stmts_added``         statements absent before and present after

An example *passes* when it compiles, keeps every def and play/wait count, adds no
undefined name and drops no animation. ``damage`` is
``stmts_removed + animations_dropped + len(new_undefined)``, plus 1000 when the
result no longer compiles, so the number goes up whenever a rule hurts valid code.
Harmless edits (a font-size snap) still count in ``stmts_removed``: the ratchet only
demands that the number never rises, it does not call every edit a failure.

Usage:
    python3 eval/damage.py        # prints the per-example table
"""

from __future__ import annotations

import ast
import builtins
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_EXAMPLES = _REPO_ROOT / "manimgen" / "examples"
_COMPILE_PENALTY = 1000

# Valid manimlib names that Codeguard rules emit but no example happens to use.
# Without this, a correct name would read as "undefined" just because the example
# vocabulary has never seen it. Add a name here only after checking it exists in
# manimlib.
_EXTRA_KNOWN_NAMES = frozenset({"GREY_E"})


def _shallow_dump(node: ast.stmt) -> str:
    """Dump a statement without descending into nested statement bodies."""
    parts = [type(node).__name__]
    for field, value in ast.iter_fields(node):
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            continue
        if isinstance(value, ast.AST):
            parts.append(f"{field}={ast.dump(value)}")
        elif isinstance(value, list):
            inner = ",".join(ast.dump(v) for v in value if isinstance(v, ast.AST))
            parts.append(f"{field}=[{inner}]")
        else:
            parts.append(f"{field}={value!r}")
    return "|".join(parts)


def _statements(tree: ast.AST) -> Counter:
    return Counter(_shallow_dump(n) for n in ast.walk(tree) if isinstance(n, ast.stmt))


def _defs(tree: ast.AST) -> Counter:
    kinds = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return Counter(
        (type(n).__name__, n.name) for n in ast.walk(tree) if isinstance(n, kinds)
    )


def _self_calls(tree: ast.AST, method: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == method
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
    ]


def _animations(tree: ast.AST) -> Counter:
    """Every expression handed to self.play, as an AST dump."""
    out: Counter = Counter()
    for call in _self_calls(tree, "play"):
        for arg in call.args:
            out[ast.dump(arg)] += 1
    return out


def _unbound_names(tree: ast.AST) -> set[str]:
    bound: set[str] = set(dir(builtins))
    loaded: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (loaded if isinstance(n.ctx, ast.Load) else bound).add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    return loaded - bound


def measure_damage(
    before: str, after: str, known_names: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Compare known-good source with its repaired form."""
    try:
        t_before = ast.parse(before)
    except SyntaxError:
        # Not known-good input; nothing meaningful to compare.
        return {"input_compiles": False, "compiles": False, "passes": False}
    try:
        t_after = ast.parse(after)
    except SyntaxError as exc:
        return {
            "input_compiles": True,
            "compiles": False,
            "defs_kept": False,
            "play_wait_kept": False,
            "new_undefined": [],
            "animations_dropped": 0,
            "stmts_removed": 0,
            "stmts_added": 0,
            "changed": after != before,
            "passes": False,
            "damage": _COMPILE_PENALTY,
            "detail": f"SyntaxError: {exc.msg} (line {exc.lineno})",
        }

    s_before, s_after = _statements(t_before), _statements(t_after)
    removed = sum((s_before - s_after).values())
    added = sum((s_after - s_before).values())
    defs_kept = _defs(t_before) == _defs(t_after)
    play_wait_kept = all(
        len(_self_calls(t_before, m)) == len(_self_calls(t_after, m))
        for m in ("play", "wait")
    )
    new_undefined = sorted(
        _unbound_names(t_after) - _unbound_names(t_before) - known_names
    )
    dropped = sum((_animations(t_before) - _animations(t_after)).values())
    passes = defs_kept and play_wait_kept and not new_undefined and dropped == 0
    return {
        "input_compiles": True,
        "compiles": True,
        "defs_kept": defs_kept,
        "play_wait_kept": play_wait_kept,
        "new_undefined": new_undefined,
        "animations_dropped": dropped,
        "stmts_removed": removed,
        "stmts_added": added,
        "changed": after != before,
        "passes": passes,
        "damage": removed + dropped + len(new_undefined),
        "detail": "",
    }


def run_examples(
    examples_dir: Path | str = DEFAULT_EXAMPLES,
    repair_fn: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Run the repair path over every examples/*.py and measure the damage."""
    if repair_fn is None:
        from manimgen.validator.codeguard import precheck_and_autofix

        repair_fn = precheck_and_autofix
    sources = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(Path(examples_dir).glob("*.py"))
    }
    if not sources:
        raise ValueError(f"no examples found in {examples_dir}")
    known: set[str] = set(_EXTRA_KNOWN_NAMES)
    for src in sources.values():
        try:
            known |= _unbound_names(ast.parse(src))
        except SyntaxError:
            pass
    rows = []
    for name, before in sources.items():
        row: dict[str, Any] = {"example": name}
        row.update(measure_damage(before, repair_fn(before), frozenset(known)))
        rows.append(row)
    return rows


def summarize_examples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "passed": sum(1 for r in rows if r["passes"]),
        "changed": sum(1 for r in rows if r.get("changed")),
        "total_damage": sum(r.get("damage", 0) for r in rows),
        "stmts_removed": sum(r.get("stmts_removed", 0) for r in rows),
        "animations_dropped": sum(r.get("animations_dropped", 0) for r in rows),
        "new_undefined": sum(len(r.get("new_undefined", [])) for r in rows),
        "not_compiling": sum(1 for r in rows if not r["compiles"]),
    }


def format_table(rows: list[dict[str, Any]]) -> str:
    out = ["example | changed | pass | damage | removed | dropped | new undefined"]
    for r in rows:
        out.append(
            f"{r['example']} | {r.get('changed')} | {r['passes']} | "
            f"{r.get('damage')} | {r.get('stmts_removed')} | "
            f"{r.get('animations_dropped')} | "
            f"{','.join(r.get('new_undefined', [])) or '-'}"
        )
    return "\n".join(out)


if __name__ == "__main__":
    _rows = run_examples()
    print(format_table(_rows))
    print(summarize_examples(_rows))
