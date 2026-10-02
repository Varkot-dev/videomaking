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


def _is_headless_linux() -> bool:
    """True on Linux with neither an X11 nor a Wayland display available."""
    if not sys.platform.startswith("linux"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _is_display_error(stderr: str) -> bool:
    """True when an import traceback is pyglet failing to reach a display."""
    return "NoSuchDisplayException" in stderr or "Cannot connect to" in stderr


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
        elif _is_display_error(stderr) and _is_headless_linux():
            # Not a broken install: manimlib opens a pyglet window at import time and
            # there is no X/Wayland display. Rendering works under a virtual display.
            if shutil.which("xvfb-run"):
                _warn(
                    "manimlib import (no display)",
                    "headless Linux (no DISPLAY/WAYLAND_DISPLAY): pyglet cannot open a window",
                    "run the command under a virtual display: xvfb-run -a "
                    "python3 -m manimgen ...  (same for manimgl and pytest)",
                )
            else:
                _fail(
                    "manimlib import (no display)",
                    "headless Linux (no DISPLAY/WAYLAND_DISPLAY) and xvfb-run is not installed",
                    "sudo apt-get install xvfb, then run the command under: xvfb-run -a <command>",
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
    unreadable: list[str] = []
    validator_dir = os.path.join(PROJECT_ROOT, "manimgen", "validator")
    for root, _dirs, files in os.walk(validator_dir):
        for fn in files:
            if fn.endswith(".py"):
                path = os.path.join(root, fn)
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        if '"--fps"' in f.read():
                            hits.append(os.path.relpath(path, PROJECT_ROOT))
                except OSError:
                    unreadable.append(os.path.relpath(path, PROJECT_ROOT))
    if hits:
        _fail(
            "broken --fps flag",
            f"--fps found in {', '.join(hits)} — crashes manimgl 1.7.2 (int/str)",
            "render via validator/render_command.build_manimgl_command(); never pass --fps",
        )
    elif not unreadable:
        _ok("no broken --fps flag in render code")
    if unreadable:
        _warn(
            "--fps scan incomplete",
            f"could not read {', '.join(unreadable)}; a broken --fps flag there would go unnoticed",
            "fix the file permissions, then re-run scripts/env_doctor.py",
        )


def check_planner_uses_json_mode() -> None:
    """Landmine #4: planner crashed on malformed LLM JSON; json_mode prevents it."""
    planner = os.path.join(PROJECT_ROOT, "manimgen", "planner", "lesson_planner.py")
    try:
        with open(planner, encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
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
    """manimgl renders, ffmpeg muxes audio/video, ffprobe measures durations.

    shutil.which honours PATHEXT, so this finds ffmpeg.exe etc. on Windows too.
    """
    for tool in ("manimgl", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            fix = (
                "pip install 'manimgl==1.7.2'"
                if tool == "manimgl"
                else "install ffmpeg (it ships ffprobe) and put its bin/ directory on PATH"
            )
            _warn(tool, "not found on PATH", fix)
        else:
            _ok(f"{tool} on PATH")


def _llm_settings() -> tuple[str, str]:
    """(provider, claude_cli_path) resolved the same way manimgen/llm.py does.

    LLM_PROVIDER in the environment wins, then config.yaml, then the defaults.
    """
    provider, cli_path = "claude_cli", "claude"
    try:
        # llm.py calls load_dotenv() at import, so a LLM_PROVIDER set in .env
        # applies to real runs and must apply to this check too.
        from dotenv import load_dotenv

        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    except ImportError:
        pass
    try:
        import yaml

        with open(os.path.join(PROJECT_ROOT, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        provider = str(cfg.get("llm_provider", provider))
        cli_path = str((cfg.get("llm") or {}).get("claude_cli_path", cli_path))
    except Exception:
        pass  # missing yaml/config: fall back to the llm.py defaults
    env = os.environ.get("LLM_PROVIDER", "").strip().lower()
    return (env or provider.strip().lower()), cli_path


def check_claude_cli() -> None:
    """The default claude_cli provider shells out to `claude -p`; it must be on PATH."""
    provider, cli_path = _llm_settings()
    if provider != "claude_cli":
        _ok(f"llm_provider is {provider!r} (Claude Code CLI not required)")
        return
    if shutil.which(cli_path) is None:
        _warn(
            "claude CLI",
            f"llm_provider is claude_cli but {cli_path!r} was not found on PATH",
            "install Claude Code (https://claude.com/claude-code) and run `claude` once to "
            "log in, or set llm.claude_cli_path in config.yaml to the full path",
        )
    else:
        _ok(f"{cli_path} (Claude Code CLI) on PATH")


def main() -> int:
    print(f"{GRN}[env-doctor]{RST} verifying manimgen setup landmines…", file=sys.stderr)
    check_manimlib_imports()
    check_setuptools_has_pkg_resources()
    check_manimgen_entrypoint()
    check_no_broken_fps_flag()
    check_planner_uses_json_mode()
    check_render_toolchain()
    check_claude_cli()

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
