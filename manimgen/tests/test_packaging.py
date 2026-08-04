"""Packaging tests — the console entry points must resolve on a clean install.

`setup.py` previously declared ``package_dir={"": "manimgen"}``, which told
setuptools that packages live *inside* ``manimgen/``. It therefore installed the
subpackages as top level — ``top_level.txt`` read
``editor, generator, input, planner, renderer, validator`` — while ``manimgen``
itself was never installed.

Two consequences, neither visible in development:

1. ``manimgen --help`` died with ``ModuleNotFoundError: No module named
   'manimgen'`` on a clean install, even though ``import manimgen`` appeared to
   succeed. The editable ``.pth`` pointed one directory too deep, so
   ``import manimgen`` resolved to an empty *namespace* package with
   ``__file__ = None`` and only failed on the first submodule access.
2. It squatted generic names — ``input``, ``editor``, ``planner`` — in the
   global namespace of any environment installing this package.

Development never caught it because running from inside ``manimgen/`` puts the
correct directory on ``sys.path`` anyway, shadowing the broken entry. CI never
caught it because CI runs ``pip install -e .`` and then invokes pytest, which
imports the package by path rather than through the console script.

These tests check the packaging metadata and entry-point resolution directly, so
a regression fails the build rather than waiting for a user to hit it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = PACKAGE_ROOT / "setup.py"


def _setup_kwargs() -> dict:
    """Parse setup.py's setup(...) call without executing it."""
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setup"
        ):
            return {kw.arg: kw for kw in node.keywords if kw.arg}
    raise AssertionError("no setup(...) call found in setup.py")


@pytest.mark.unit
def test_setup_does_not_reroot_the_package_directory() -> None:
    """`package_dir={"": "manimgen"}` is the exact misconfiguration to avoid.

    It flattens the package, installing subpackages at top level and leaving
    `manimgen` itself absent.
    """
    kwargs = _setup_kwargs()
    package_dir = kwargs.get("package_dir")
    if package_dir is None:
        return

    literal = ast.literal_eval(package_dir.value)
    assert literal.get("") != "manimgen", (
        'setup.py sets package_dir={"": "manimgen"}, which installs the '
        "subpackages as top level and omits the manimgen package itself. The "
        "console scripts then fail with ModuleNotFoundError on a clean install."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [
        "manimgen.cli:main",
        "manimgen.editor.server:main",
    ],
)
def test_console_script_targets_are_importable(target: str) -> None:
    """Every console_scripts target must resolve to a real callable.

    An entry point naming a module path that does not exist installs fine and
    fails only when a user runs the command.
    """
    module_path, _, attr = target.partition(":")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - failure path
        pytest.fail(f"console script target {target!r} is not importable: {exc}")

    # A namespace package imports successfully but has __file__ = None, which is
    # precisely how the broken install masqueraded as working.
    assert getattr(module, "__file__", None) is not None, (
        f"{module_path} resolved to a namespace package (__file__ is None) "
        "rather than a real module — the install is likely rooted one "
        "directory too deep"
    )

    assert callable(getattr(module, attr, None)), (
        f"console script target {target!r} does not resolve to a callable"
    )


@pytest.mark.unit
def test_declared_entry_points_match_the_tested_ones() -> None:
    """Guard against an entry point being added without a matching test."""
    kwargs = _setup_kwargs()
    entry_points = kwargs.get("entry_points")
    assert entry_points is not None, "setup.py declares no entry_points"

    declared = ast.literal_eval(entry_points.value)["console_scripts"]
    targets = {spec.split("=", 1)[1].strip() for spec in declared}

    tested = {"manimgen.cli:main", "manimgen.editor.server:main"}
    assert targets == tested, (
        f"console_scripts changed: {sorted(targets)}. Update the parametrised "
        "list in test_console_script_targets_are_importable so every command "
        "stays covered."
    )


@pytest.mark.unit
def test_manimgen_is_a_real_package_not_a_namespace() -> None:
    """`import manimgen` must yield a real package.

    The broken editable install produced a namespace package here, which is why
    a bare `import manimgen` looked fine while `from manimgen.cli import main`
    failed.
    """
    import manimgen

    assert getattr(manimgen, "__file__", None) is not None, (
        "manimgen imported as a namespace package (__file__ is None). The "
        "editable install path is rooted one directory too deep."
    )
