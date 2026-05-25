#!/usr/bin/env python3
"""Env doctor — actively verifies the recurring setup landmines this project hits.

Every check here corresponds to a real failure we diagnosed and fixed. The point
is enforcement, not documentation: run this at session start (and before a
pipeline run) so a known, already-solved breakage never silently wastes a run
again.

Exit codes:
  0  all checks pass
  1  one or more BLOCKING checks failed (env cannot render) — fix before running
  (WARN-level issues never change the exit code; they print and move on.)

Each failing check prints the exact remediation command. Keep this list in sync
with docs/KNOWN_ISSUES.md — when a new recurring landmine is found, add a check
here so it can never recur.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ANSI (skip if not a tty)
_TTY = sys.stderr.isatty()
RED = "\033[31m" if _TTY else ""
YEL = "\033[33m" if _TTY else ""
GRN = "\033[32m" if _TTY else ""
RST = "\033[0m" if _TTY else ""

_blocking_failures: list[str] = []
_warnings: list[str] = []


def _ok(msg: str) -> None:
    print(f"{GRN}  ok{RST}   {msg}", file=sys.stderr)


def _fail(check: str, detail: str, fix: str) -> None:
    _blocking_failures.append(check)
    print(f"{RED}  FAIL{RST} {check}: {detail}", file=sys.stderr)
    print(f"       fix: {fix}", file=sys.stderr)


def _warn(check: str, detail: str, fix: str) -> None:
    _warnings.append(check)
    print(f"{YEL}  warn{RST} {check}: {detail}", file=sys.stderr)
    print(f"       fix: {fix}", file=sys.stderr)


def check_manimlib_imports() -> None:
    """Landmine #1: editable install pointed at a moved/dead path."""
    if importlib.util.find_spec("manimlib") is None:
        _fail(
            "manimlib import",
            "manimlib is not importable (likely an editable install pointing at a moved path)",
            "pip install 'manimgl==1.7.2'  (non-editable, from PyPI)",
        )
        return
    try:
        subprocess.run(
            [sys.executable, "-c", "import manimlib"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        _ok("manimlib imports")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace")
        if "pkg_resources" in stderr:
            _fail(
                "manimlib import (pkg_resources)",
                "manimlib imports pkg_resources, which setuptools 81+ removed",
                "pip install 'setuptools<81'",
            )
        else:
            _fail("manimlib import", stderr.strip().splitlines()[-1:][0] if stderr else "unknown",
                  "investigate the traceback above")


def check_setuptools_has_pkg_resources() -> None:
    """Landmine #2: setuptools 81+ dropped pkg_resources; manimgl 1.7.2 needs it."""
    if importlib.util.find_spec("pkg_resources") is None:
        _fail(
            "pkg_resources",
            "missing (setuptools 81+ removed it); manimgl 1.7.2's __init__ imports it",
            "pip install 'setuptools<81'",
        )
    else:
        _ok("pkg_resources available")


def check_manimgen_entrypoint() -> None:
    """Landmine #3: editable .pth pointed at the package dir, not its parent."""
    try:
        subprocess.run(
            [sys.executable, "-c", "from manimgen.cli import main"],
            check=True,
            capture_output=True,
            timeout=60,
            cwd="/",  # neutral dir: don't let cwd mask a broken install
        )
        _ok("manimgen.cli imports from a neutral directory")
    except subprocess.CalledProcessError:
        _fail(
            "manimgen entrypoint",
            "manimgen.cli not importable outside the project dir (editable .pth likely wrong)",
            "pip install -e . from the project root, then point the .pth at the PARENT of the package dir",
        )


def check_no_broken_fps_flag() -> None:
    """Landmine #5: manimgl 1.7.2 --fps crashes (int/str). It must not be in our argv."""
    hits: list[str] = []
    validator_dir = os.path.join(PROJECT_ROOT, "manimgen", "validator")
    for root, _dirs, files in os.walk(validator_dir):
        for fn in files:
            if fn.endswith(".py"):
                path = os.path.join(root, fn)
                try:
                    with open(path) as f:
                        if '"--fps"' in f.read():
                            hits.append(os.path.relpath(path, PROJECT_ROOT))
                except OSError:
                    continue
    if hits:
        _fail(
            "broken --fps flag",
            f"--fps found in {', '.join(hits)} — crashes manimgl 1.7.2 (int/str)",
            "render via validator/render_command.build_manimgl_command(); never pass --fps",
        )
    else:
        _ok("no broken --fps flag in render code")


def check_planner_uses_json_mode() -> None:
    """Landmine #4: planner crashed on malformed LLM JSON; json_mode prevents it."""
    planner = os.path.join(PROJECT_ROOT, "manimgen", "planner", "lesson_planner.py")
    try:
        with open(planner) as f:
            src = f.read()
    except OSError:
        return  # planner moved; not this check's job to report
    if "json_mode=True" not in src:
        _warn(
            "planner JSON mode",
            "lesson_planner.py has no json_mode=True chat() calls",
            "pass json_mode=True on planner chat() calls so Gemini emits valid JSON",
        )
    else:
        _ok("planner uses Gemini json_mode")


def check_render_toolchain() -> None:
    """ffmpeg/manimgl binaries must be on PATH for rendering."""
    for tool in ("manimgl", "ffmpeg"):
        if shutil.which(tool) is None:
            _warn(tool, "not found on PATH", f"install {tool} / ensure it is on PATH")
        else:
            _ok(f"{tool} on PATH")


def main() -> int:
    print(f"{GRN}[env-doctor]{RST} verifying manimgen setup landmines…", file=sys.stderr)
    check_manimlib_imports()
    check_setuptools_has_pkg_resources()
    check_manimgen_entrypoint()
    check_no_broken_fps_flag()
    check_planner_uses_json_mode()
    check_render_toolchain()

    if _blocking_failures:
        print(
            f"{RED}[env-doctor] {len(_blocking_failures)} blocking issue(s) — "
            f"fix before running the pipeline.{RST}",
            file=sys.stderr,
        )
        return 1
    if _warnings:
        print(f"{YEL}[env-doctor] {len(_warnings)} warning(s) — non-blocking.{RST}", file=sys.stderr)
    else:
        print(f"{GRN}[env-doctor] all checks passed.{RST}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
