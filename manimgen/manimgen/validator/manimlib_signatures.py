"""Type-aware kwarg-introspection shadow (Phase 2 of the codeguard pivot).

Sibling to ``manimlib_symbols.py`` (which checks call-target NAMES). This module
checks constructor KWARGS: for each ``ClassName(...)`` call, resolve the class from
the pinned ``manimlib``, introspect its real ``__init__`` signature across the MRO,
and flag a kwarg ONLY when it is *provably* invalid.

Report-only / shadow mode: it returns a list of flagged kwargs for logging and
tests; it never blocks, rewrites, or degrades a render. Fully fail-open — if
``manimlib`` cannot be imported (CI / no GL) the check is a no-op.

Soundness rule (see docs/DESIGN_codeguard_introspection_pivot.md). Flag kwarg ``k``
on class ``C`` as invalid IFF all hold:
  1. ``k`` is not named in any ``__init__`` across C's MRO, AND
  2. ``k`` is not in the VMobject named-param set (the whole VMobject param set is
     always valid for any VMobject descendant), AND
  3. ``C`` is not a known lateral-forwarder (consumes ``**kwargs`` then forwards
     them to another constructor by a route static introspection can't follow), AND
  4. the ``**kwargs`` forwarding chain terminates at a CLOSED signature
     (``Mobject.__init__`` in manimlib — no ``**kwargs``).

When any of these can't be established, we stay SILENT. A false positive poisons the
shadow data we'd use to justify enforcement, so absence of proof is never a flag.
"""

from __future__ import annotations

import ast
import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)

# Phase 3 enforcement flag. Default OFF — the strip mechanism stays dormant until
# shadow-log data confirms zero false positives (see the design doc's gate). When
# enabled, provably-invalid kwargs are REMOVED (never renamed — removal cannot
# create a duplicate kwarg, so #55 is structurally impossible).
_ENFORCE_ENV_VAR = "MANIMGEN_KWARG_ENFORCE"
_FALSEY = frozenset({"", "0", "false", "off", "no"})


def enforcement_enabled() -> bool:
    """True when MANIMGEN_KWARG_ENFORCE is set to a truthy value (default False)."""
    return os.environ.get(_ENFORCE_ENV_VAR, "").strip().lower() not in _FALSEY

# Classes whose __init__ consumes **kwargs and forwards them LATERALLY (not via
# super()) to another constructor that static MRO introspection cannot follow.
# Flagging a kwarg on these would be a false positive — never flag them.
_LATERAL_FORWARDERS: frozenset[str] = frozenset(
    {"BulletedList", "VectorizedPoint", "Checkmark", "Exmark"}
)


@dataclass
class KwargSpec:
    """Introspected acceptance info for a class's constructor across its MRO."""
    named: set[str] = field(default_factory=set)  # all named kwargs in the MRO
    chain_is_open: bool = False  # some __init__ in the MRO has **kwargs
    chain_terminates_closed: bool = True  # the **kwargs chain bottoms out closed
    introspectable: bool = True  # every __init__ signature() succeeded


@dataclass(frozen=True)
class FlaggedKwarg:
    class_name: str
    kwarg: str
    lineno: int


@lru_cache(maxsize=512)
def accepted_kwargs(cls: type) -> KwargSpec:
    """Union of named kwargs across cls's MRO + whether the **kwargs chain is open
    and whether it provably terminates at a closed signature.

    Walks ``cls.__mro__`` (excluding ``object``), inspecting each class that defines
    its own ``__init__``. A class with no ``**kwargs`` is a *closed* link; if the
    most-base custom ``__init__`` is closed, the chain terminates closed.
    """
    spec = KwargSpec()
    last_custom_open: bool | None = None  # openness of the most-base custom __init__
    for klass in cls.__mro__:
        if klass is object:
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue  # inherited __init__; contributes nothing of its own
        try:
            sig = inspect.signature(init)
        except (ValueError, TypeError):
            spec.introspectable = False
            continue
        has_var_kw = False
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
                spec.named.add(p.name)
            elif p.kind == p.VAR_KEYWORD:
                has_var_kw = True
                spec.chain_is_open = True
        # __mro__ is most-derived first, so the LAST custom __init__ we see is the
        # most-base one — its openness decides whether the chain terminates closed.
        last_custom_open = has_var_kw
    # Chain terminates closed iff the most-base custom __init__ has no **kwargs.
    spec.chain_terminates_closed = (last_custom_open is False)
    return spec


# VMobject's full named-param set is always valid for any VMobject descendant.
# Resolved lazily from the real class when available; falls back to the known set.
_VMOBJECT_PARAMS_FALLBACK: frozenset[str] = frozenset({
    "color", "fill_color", "fill_opacity", "stroke_color", "stroke_opacity",
    "stroke_width", "stroke_behind", "background_image_file", "long_lines",
    "joint_type", "flat_stroke", "scale_stroke_with_zoom",
    "use_simple_quadratic_approx", "anti_alias_width", "fill_border_width",
})


@lru_cache(maxsize=1)
def _vmobject_param_set() -> frozenset[str]:
    sigtab = load_manimlib_signatures()
    if sigtab is None:
        return _VMOBJECT_PARAMS_FALLBACK
    vm = sigtab.get("VMobject")
    if vm is None:
        return _VMOBJECT_PARAMS_FALLBACK
    return frozenset(accepted_kwargs(vm).named)


@lru_cache(maxsize=1)
def _vmobject_cls() -> type | None:
    sigtab = load_manimlib_signatures()
    return sigtab.get("VMobject") if sigtab else None


def is_provably_invalid_kwarg(
    cls: type,
    kwarg: str,
    lateral_exceptions: frozenset[str] | set[str] = _LATERAL_FORWARDERS,
) -> bool:
    """Return True only when ``kwarg`` is provably rejected by ``cls`` (see rule)."""
    if cls.__name__ in lateral_exceptions:
        return False  # routes kwargs somewhere introspection can't follow

    spec = accepted_kwargs(cls)
    if not spec.introspectable:
        return False  # opaque signature -> stay silent
    if kwarg in spec.named:
        return False  # named somewhere in the MRO -> valid

    # If this is a VMobject descendant, the whole VMobject param set is valid.
    vm = _vmobject_cls()
    if vm is not None and issubclass(cls, vm) and kwarg in _vmobject_param_set():
        return False

    # Only sound to reject when the **kwargs chain provably bottoms out closed.
    if not spec.chain_terminates_closed:
        return False

    return True


def _import_alias_map(tree: ast.AST) -> dict[str, str]:
    """Map local import aliases back to their manimlib symbol name.

    ``from manimlib import Square as Sq`` -> {"Sq": "Square"}. ``import manimlib`` and
    ``from manimlib import *`` add nothing here (bare names resolve directly)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
    return aliases


@lru_cache(maxsize=1)
def load_manimlib_signatures() -> dict[str, type] | None:
    """Return {public_name: class_obj} for every class exported by pinned manimlib.

    None (fail-open) when manimlib can't import. Mirrors manimlib_symbols.py's
    sys.argv-blanking guard (manimlib parses argv at import and may raise SystemExit).
    """
    saved_argv = sys.argv
    try:
        sys.argv = [saved_argv[0] if saved_argv else "manimgen"]
        import manimlib  # noqa: PLC0415
    except (Exception, SystemExit) as exc:
        logger.info(
            "[codeguard][kwarg-shadow] manimlib unavailable (%s) — "
            "kwarg introspection disabled this run",
            exc,
        )
        return None
    finally:
        sys.argv = saved_argv

    table: dict[str, type] = {}
    for name in dir(manimlib):
        if name.startswith("_"):
            continue
        obj = getattr(manimlib, name, None)
        if isinstance(obj, type):
            table[name] = obj
    return table


def _resolve_class(name: str, aliases: dict[str, str]) -> type | None:
    """Resolve a call-target name to a manimlib class object, or None."""
    sigtab = load_manimlib_signatures()
    if sigtab is None:
        return None
    real = aliases.get(name, name)
    return sigtab.get(real)


def shadow_check_kwargs(code: str) -> list[FlaggedKwarg]:
    """Return constructor kwargs in ``code`` that are provably invalid for the
    resolved manimlib class. Report-only; fail-open; never raises."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # precheck's job, not ours

    aliases = _import_alias_map(tree)
    flagged: list[FlaggedKwarg] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # constructors only: bare Name call targets (Foo(...)), not obj.method(...)
        if not isinstance(node.func, ast.Name):
            continue
        cls = _resolve_class(node.func.id, aliases)
        if cls is None:
            continue
        for kw in node.keywords:
            if kw.arg is None:
                # **splat — we can't know what's inside; skip the whole call's kwargs
                break
            if is_provably_invalid_kwarg(cls, kw.arg):
                flagged.append(
                    FlaggedKwarg(class_name=node.func.id, kwarg=kw.arg,
                                 lineno=getattr(node, "lineno", 0))
                )
    return flagged


def _line_col_to_offset(lines: list[str], lineno: int, col: int) -> int:
    """Convert a 1-based (lineno, 0-based col) AST position to an absolute char
    offset in the original source string (lines kept with their newlines)."""
    return sum(len(lines[i]) for i in range(lineno - 1)) + col


def strip_invalid_kwargs(code: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove provably-invalid constructor kwargs from ``code``.

    Returns ``(new_code, removed)`` where ``removed`` is a list of
    ``(class_name, kwarg)``. The strip is surgical: each invalid keyword is excised
    by its exact AST span (plus one bordering comma), right-to-left so earlier spans
    stay valid. Removal — never rename — so it cannot create a duplicate kwarg.
    Comments and formatting elsewhere are untouched (no ``ast.unparse``). Fail-open:
    on a syntax error or no manimlib, returns the input unchanged. Never raises.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, []

    aliases = _import_alias_map(tree)
    # (start_offset, end_offset, class_name, kwarg) for each keyword to remove.
    spans: list[tuple[int, int, str, str]] = []
    src_lines = code.splitlines(keepends=True)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        cls = _resolve_class(node.func.id, aliases)
        if cls is None:
            continue
        for kw in node.keywords:
            if kw.arg is None:
                break  # **splat — skip the whole call
            if not is_provably_invalid_kwarg(cls, kw.arg):
                continue
            # ast.keyword carries col_offset/end_col_offset for the full arg=value
            # span (Python 3.9+). Fail-open if positions are missing.
            if getattr(kw, "col_offset", None) is None or getattr(kw, "end_col_offset", None) is None:
                continue
            start = _line_col_to_offset(src_lines, kw.lineno, kw.col_offset)
            end = _line_col_to_offset(src_lines, kw.end_lineno, kw.end_col_offset)
            spans.append((start, end, node.func.id, kw.arg))

    if not spans:
        return code, []

    removed: list[tuple[str, str]] = []
    out = code
    # Apply right-to-left so each excision leaves earlier offsets intact.
    for start, end, class_name, kwarg in sorted(spans, key=lambda s: s[0], reverse=True):
        # Absorb one bordering comma (and surrounding spaces) so we don't leave a
        # dangling ", ," or "(, ". Prefer the preceding comma; else the following.
        lo, hi = start, end
        j = lo - 1
        while j >= 0 and out[j] in " \t":
            j -= 1
        if j >= 0 and out[j] == ",":
            lo = j  # eat the preceding comma
        else:
            k = hi
            while k < len(out) and out[k] in " \t":
                k += 1
            if k < len(out) and out[k] == ",":
                hi = k + 1  # eat the following comma
        out = out[:lo] + out[hi:]
        removed.append((class_name, kwarg))

    removed.reverse()  # report in source order
    return out, removed
