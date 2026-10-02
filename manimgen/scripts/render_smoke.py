#!/usr/bin/env python3
"""Render smoke test: does this machine actually render a manimgen video?

Run it on any machine (Windows, macOS, Linux), no admin rights needed:

    python scripts/render_smoke.py            # full run incl. a 1080p render
    python scripts/render_smoke.py --quick    # skip the slow 1080p render
    python scripts/render_smoke.py --keep     # keep the rendered files

It never calls an LLM and never touches the network. Steps:

  a. Python version and virtualenv
  b. ffmpeg / ffprobe on PATH
  c. manimlib (manimgl) import and pkg_resources
  d. OpenGL 3.3+ context via moderngl (reports the GPU / software renderer)
  e. LaTeX (optional, WARN only)
  f. A timed real render of a tiny scene at 480p, then at the pipeline default

Each step prints PASS / FAIL / WARN / SKIP with a one-line fix hint. The exit
code is 0 only when no FAIL-level check failed.

The render reuses the pipeline's own command builder, render environment and
UTF-8 handling (manimgen.validator), so a pass here means the pipeline's
render path works on this machine. Quality is the one thing swapped: the
builder takes it from config.yaml, and the 480p run substitutes ``-l``.
manimgl 1.7.2 cannot take ``--fps`` (see manimgen/validator/render_command.py),
so renders use manimgl's own default fps; the pipeline normalizes it later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"

MIN_PYTHON = (3, 11)
MIN_GL = (3, 3)

SCENE_CLASS = "ManimgenSmokeScene"

# A few shapes, a Text (exercises pango) and a short Transform, about 2.5s.
SCENE_SOURCE = """\
from manimlib import *


class ManimgenSmokeScene(Scene):
    def construct(self):
        circle = Circle(color=BLUE)
        square = Square(color=YELLOW).shift(2 * RIGHT)
        label = Text("manimgen smoke test").to_edge(UP)
        self.play(ShowCreation(circle), FadeIn(label), run_time=0.8)
        self.play(FadeIn(square), run_time=0.4)
        self.play(Transform(circle, square.copy().shift(4 * LEFT)), run_time=0.8)
        self.wait(0.5)
"""

# Run in a child process: a missing or crashing GL driver must not take this
# script down with it, and the child prints exactly one JSON line.
GL_PROBE_SOURCE = r"""
import json, sys
out = {"ok": False, "errors": []}
try:
    import moderngl
except Exception as e:
    out["errors"].append("import moderngl failed: %s: %s" % (type(e).__name__, e))
    print(json.dumps(out)); sys.exit(0)
attempts = [{}]
if sys.platform.startswith("linux"):
    attempts.append({"backend": "egl"})
for kwargs in attempts:
    try:
        ctx = moderngl.create_standalone_context(require=330, **kwargs)
    except Exception as e:
        out["errors"].append("%s: %s" % (kwargs or "default backend", e))
        continue
    info = ctx.info
    out.update(ok=True, version=info.get("GL_VERSION", ""),
               renderer=info.get("GL_RENDERER", ""), vendor=info.get("GL_VENDOR", ""),
               backend=str(kwargs.get("backend", "default")))
    try:
        ctx.release()
    except Exception:
        pass
    break
print(json.dumps(out))
"""

SOFTWARE_RENDERER_MARKERS = (
    "llvmpipe",
    "softpipe",
    "swrast",
    "software",
    "gdi generic",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def parse_fraction(text: str | None) -> float | None:
    """Parse an ffprobe rate such as ``"30/1"`` or ``"30000/1001"``."""
    if not text:
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(text)
    except ValueError:
        return None


def parse_ffprobe_json(raw: str) -> dict:
    """Reduce ``ffprobe -print_format json -show_streams -show_format`` output.

    Returns ``{has_video, width, height, fps, duration, has_audio}``. Raises
    ValueError when ``raw`` is not usable ffprobe JSON.
    """
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"ffprobe output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("ffprobe output is not a JSON object")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = None
    for source in (data.get("format") or {}, video or {}):
        try:
            duration = float(source["duration"])
            break
        except (KeyError, TypeError, ValueError):
            continue
    if video is None:
        return {
            "has_video": False,
            "width": None,
            "height": None,
            "fps": None,
            "duration": duration,
            "has_audio": has_audio,
        }
    fps = parse_fraction(video.get("avg_frame_rate")) or parse_fraction(
        video.get("r_frame_rate")
    )
    return {
        "has_video": True,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": fps,
        "duration": duration,
        "has_audio": has_audio,
    }


def evaluate_probe(
    info: dict, expected_height: int | None, min_duration: float = 1.0
) -> tuple[str, str]:
    """Judge a parsed probe. Returns ``(status, one-line detail)``."""
    if not info.get("has_video"):
        return FAIL, "output file has no video stream"
    w, h, fps, dur = info["width"], info["height"], info["fps"], info["duration"]
    fps_txt = f"{fps:.2f}fps" if fps else "unknown fps"
    dur_txt = f"{dur:.2f}s" if dur is not None else "unknown duration"
    detail = f"{w}x{h} {fps_txt} {dur_txt}"
    if dur is None or dur < min_duration:
        return FAIL, f"{detail} (expected at least {min_duration:.1f}s of video)"
    if expected_height and h != expected_height:
        return WARN, f"{detail} (expected height {expected_height})"
    return PASS, detail


def swap_quality_flag(cmd: list[str], old: str, new: str) -> list[str]:
    """Return ``cmd`` with the quality flag ``old`` replaced by ``new``."""
    out = list(cmd)
    if old in out:
        out[out.index(old)] = new
    else:
        out.insert(out.index("-w") + 1 if "-w" in out else len(out), new)
    return out


def parse_gl_version(text: str) -> tuple[int, int] | None:
    """Extract (major, minor) from a GL_VERSION string like ``4.5 (Core ...)``."""
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def evaluate_gl(payload: dict | None, platform: str | None = None) -> Check:
    """Turn the GL probe's JSON payload into a Check."""
    platform = platform or sys.platform
    if not payload:
        return Check(
            "OpenGL",
            FAIL,
            "the OpenGL probe produced no result (it crashed or timed out)",
            _gl_hint(platform),
        )
    if not payload.get("ok"):
        errs = "; ".join(payload.get("errors") or ["unknown error"])
        return Check(
            "OpenGL",
            FAIL,
            f"could not create an OpenGL 3.3+ context: {errs[:300]}",
            _gl_hint(platform),
        )
    version = payload.get("version", "")
    renderer = payload.get("renderer", "") or "unknown renderer"
    parsed = parse_gl_version(version)
    desc = f"GL {version.strip()} on {renderer.strip()}"
    if parsed is None or parsed < MIN_GL:
        return Check(
            "OpenGL",
            FAIL,
            f"{desc} is older than OpenGL 3.3",
            _gl_hint(platform),
        )
    if any(m in renderer.lower() for m in SOFTWARE_RENDERER_MARKERS):
        return Check(
            "OpenGL",
            WARN,
            f"{desc} (software rendering: works but slow)",
            "Update the graphics driver for hardware GL if you can; otherwise "
            "expect slow renders and prefer quality 'l' or 'm' in config.yaml.",
        )
    return Check("OpenGL", PASS, desc)


def _gl_hint(platform: str) -> str:
    if platform.startswith("win"):
        return (
            "No admin: put Mesa's opengl32.dll (pal1000/mesa-dist-win, x64 folder) "
            "next to python.exe. With admin: install the Intel graphics driver."
        )
    if platform == "darwin":
        return "macOS provides OpenGL 4.1; reinstall moderngl: pip install -U moderngl"
    return (
        "Install Mesa GL/EGL (apt: libgl1 libegl1 libglu1-mesa) or run under "
        "xvfb-run for a headless machine."
    )


def format_summary(checks: list[Check]) -> str:
    """ASCII summary table (safe for any console encoding)."""
    width = max([len(c.name) for c in checks] + [4])
    lines = ["", "=" * 72, "SUMMARY", "=" * 72]
    for c in checks:
        lines.append(f"  {c.status:<5} {c.name:<{width}}  {c.detail}".rstrip())
    counts = {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL)}
    skipped = sum(1 for c in checks if c.status == SKIP)
    lines.append("-" * 72)
    lines.append(
        f"  {counts[PASS]} passed, {counts[WARN]} warnings, "
        f"{counts[FAIL]} failed, {skipped} skipped"
    )
    verdict = (
        "RESULT: this machine can render."
        if exit_code(checks) == 0
        else "RESULT: NOT ready, fix the FAIL lines above."
    )
    lines.append(f"  {verdict}")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    """0 only when no check has FAIL status (WARN and SKIP never fail the run)."""
    return 1 if any(c.status == FAIL for c in checks) else 0


def format_check(c: Check) -> str:
    text = f"[{c.status}] {c.name}: {c.detail}"
    if c.hint and c.status in (FAIL, WARN):
        text += f"\n       fix: {c.hint}"
    return text


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and its children (same idea as manimgen.llm)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def run_tree(
    cmd: list[str],
    *,
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str, bool]:
    """Run ``cmd`` (no shell). Returns (returncode, stdout, stderr, timed_out).

    On timeout the whole process tree is killed. A command that cannot be
    started returns ``(None, "", message, False)`` instead of raising.
    """
    popen_kwargs: dict = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=env,
            **popen_kwargs,
        )
    except OSError as e:
        return None, "", f"could not start {cmd[0]!r}: {e}", False
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return None, out or "", err or "", True


def _tail(text: str, lines: int = 12) -> str:
    kept = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join("         " + ln for ln in kept[-lines:])


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_python(version_info=None, in_venv: bool | None = None) -> list[Check]:
    vi = version_info or sys.version_info
    if in_venv is None:
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    ver = f"{vi[0]}.{vi[1]}.{vi[2]}"
    out = []
    if (vi[0], vi[1]) >= MIN_PYTHON:
        out.append(Check("Python", PASS, f"{ver} ({sys.executable})"))
    else:
        out.append(
            Check(
                "Python",
                FAIL,
                f"{ver} is older than {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
                "Install Python 3.11 (python.org, per-user install needs no admin).",
            )
        )
    if in_venv:
        out.append(Check("Virtualenv", PASS, "running inside a virtualenv"))
    else:
        out.append(
            Check(
                "Virtualenv",
                WARN,
                "not inside a virtualenv (packages go to the global/user site)",
                "python -m venv .venv  then activate it (optional, recommended).",
            )
        )
    return out


def check_ffmpeg_tools(which=shutil.which) -> list[Check]:
    out = []
    for tool in ("ffmpeg", "ffprobe"):
        exe = which(tool)
        if not exe:
            out.append(
                Check(
                    tool,
                    FAIL,
                    "not found on PATH",
                    "Windows no admin: download a static build from gyan.dev or "
                    "BtbN, unzip, add its bin folder to your user PATH. "
                    "macOS: brew install ffmpeg. Linux: apt install ffmpeg.",
                )
            )
            continue
        rc, so, se, timed_out = run_tree([exe, "-version"], timeout=30)
        line = _first_line(so)
        if rc != 0 or not line:
            why = "timed out" if timed_out else (_first_line(se) or f"exit code {rc}")
            out.append(
                Check(
                    tool,
                    FAIL,
                    f"found at {exe} but could not run: {why}",
                    "Re-download ffmpeg; the binary may be corrupt or blocked.",
                )
            )
        else:
            out.append(Check(tool, PASS, line[:90]))
    return out


def check_manimlib() -> list[Check]:
    out = []
    code = "import importlib.metadata as m, manimlib; print(m.version('manimgl'))"
    rc, so, se, timed_out = run_tree([sys.executable, "-c", code], timeout=180)
    if rc == 0:
        out.append(Check("manimlib", PASS, f"manimgl {_first_line(so)} imports"))
    else:
        text = "timed out after 180s" if timed_out else (se or "")
        if "pkg_resources" in text:
            hint = "pip install 'setuptools<81'"
            why = "manimlib needs pkg_resources, which setuptools 81+ removed"
        elif (
            "No module named 'manimlib'" in text or "No module named 'manimgl'" in text
        ):
            hint = "pip install -r requirements.txt  (installs manimgl==1.7.2)"
            why = "manimgl is not installed in this Python"
        elif rc is None and not timed_out:
            hint = 'Check that this Python works: python -c "print(1)"'
            why = _first_line(text)
        elif "NoSuchDisplay" in text or "Cannot connect to" in text:
            hint = (
                "No display (headless Linux): run under xvfb-run, e.g. "
                "xvfb-run -a python scripts/render_smoke.py"
            )
            why = "importing manimlib needs a display (pyglet found none)"
        else:
            hint = "pip install --force-reinstall manimgl==1.7.2"
            last = [ln for ln in text.strip().splitlines() if ln.strip()]
            why = "importing manimlib failed: " + (
                last[-1].strip()[:200] if last else ""
            )
        out.append(Check("manimlib", FAIL, why, hint))
    try:
        import importlib.util

        found = importlib.util.find_spec("pkg_resources") is not None
    except (ImportError, ValueError):
        found = False
    if found:
        out.append(Check("pkg_resources", PASS, "provided by setuptools"))
    else:
        out.append(
            Check(
                "pkg_resources",
                FAIL,
                "not importable (setuptools missing or 81+)",
                "pip install 'setuptools<81'",
            )
        )
    return out


def check_opengl() -> Check:
    rc, so, se, timed_out = run_tree(
        [sys.executable, "-c", GL_PROBE_SOURCE], timeout=60
    )
    payload = None
    for line in reversed((so or "").splitlines()):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    check = evaluate_gl(payload)
    if payload is None and (se or timed_out):
        check.detail += (
            " (" + ("timed out" if timed_out else _first_line(se)[:200]) + ")"
        )
    return check


def check_latex() -> list[Check]:
    path = os.environ.get("PATH", "")
    try:
        _ensure_project_on_path()
        from manimgen.validator.env import get_render_env

        path = get_render_env().get("PATH", path)
    except Exception:  # package not importable: fall back to plain PATH
        pass
    out = []
    for tool in ("latex", "dvisvgm"):
        exe = shutil.which(tool, path=path or None)
        if exe:
            out.append(Check(tool, PASS, exe))
        else:
            out.append(
                Check(
                    tool,
                    WARN,
                    "not found (only Tex()/MathTex scenes need it)",
                    "Windows no admin: MiKTeX per-user install. macOS: BasicTeX. "
                    "Linux: texlive-latex-base dvisvgm.",
                )
            )
    return out


def _ensure_project_on_path() -> None:
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)


def resolve_manimgl(cmd: list[str], which=shutil.which) -> list[str]:
    """Make argv[0] a real path even when the venv's Scripts dir is not on PATH."""
    if which(cmd[0]):
        return cmd
    bindir = os.path.dirname(sys.executable)
    for d in (bindir, os.path.join(bindir, "Scripts"), os.path.join(bindir, "bin")):
        for name in (cmd[0] + ".exe", cmd[0]):
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                return [cand, *cmd[1:]]
    return [sys.executable, "-m", "manimlib", *cmd[1:]]


def find_mp4(root: str, newer_than: float) -> str | None:
    best: tuple[float, str] | None = None
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith(".mp4"):
                continue
            p = os.path.join(dirpath, f)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt >= newer_than and (best is None or mt > best[0]):
                best = (mt, p)
    return best[1] if best else None


def probe_video(path: str) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ValueError("ffprobe not found")
    rc, so, se, timed_out = run_tree(
        [
            exe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path,
        ],
        timeout=60,
    )
    if rc != 0:
        raise ValueError(
            "ffprobe timed out" if timed_out else f"ffprobe failed: {_first_line(se)}"
        )
    return parse_ffprobe_json(so)


def render_step(
    label: str,
    *,
    quality_flag: str | None,
    expected_height: int | None,
    work_dir: str,
    timeout: float,
) -> tuple[Check, float | None]:
    """Render the smoke scene once through the pipeline's command builder."""
    name = f"Render {label}"
    try:
        _ensure_project_on_path()
        from manimgen import paths
        from manimgen.validator.env import get_render_env
        from manimgen.validator.render_command import (
            build_manimgl_command,
            with_utf8_io,
        )
    except Exception as e:
        return (
            Check(
                name,
                FAIL,
                f"cannot import the manimgen package: {type(e).__name__}: {e}",
                "Run from the manimgen folder after: pip install -r requirements.txt",
            ),
            None,
        )

    step_dir = os.path.join(work_dir, re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_"))
    os.makedirs(step_dir, exist_ok=True)
    scene_path = os.path.join(step_dir, "smoke_scene.py")
    with open(scene_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(SCENE_SOURCE)

    cmd = build_manimgl_command(scene_path, SCENE_CLASS)
    if quality_flag:
        cmd = swap_quality_flag(cmd, paths.render_quality_flag(), quality_flag)
    cmd = resolve_manimgl(cmd)

    started_wall = time.time() - 1.0
    t0 = time.perf_counter()
    rc, so, se, timed_out = run_tree(
        cmd, timeout=timeout, cwd=step_dir, env=with_utf8_io(get_render_env())
    )
    secs = time.perf_counter() - t0

    log = os.path.join(step_dir, "render.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\nrc={rc} timed_out={timed_out}\n")
        f.write(f"--- stdout ---\n{so}\n--- stderr ---\n{se}\n")

    if timed_out:
        return (
            Check(
                name,
                FAIL,
                f"timed out after {timeout:.0f}s (process tree killed)",
                "Very slow GL (software rendering?). Try the 480p step only (--quick).",
            ),
            secs,
        )
    if rc != 0:
        why = _first_line(se.strip().splitlines()[-1] if se.strip() else so)
        detail = f"manimgl exited {rc} after {secs:.1f}s: {why[:200]}"
        tail = _tail(se or so, 8)
        if tail:
            detail += "\n" + tail
        hint = "See render.log (use --keep). If the error mentions OpenGL, fix step d."
        if "pkg_resources" in (se or ""):
            hint = "pip install 'setuptools<81'"
        elif "NoSuchDisplay" in (se or ""):
            hint = (
                "Linux/X11 only: the render env drops XAUTHORITY; retry with "
                "MANIMGEN_RENDER_ENV_EXTRA=XAUTHORITY (e.g. under xvfb-run)."
            )
        return Check(name, FAIL, detail, hint), secs

    video = find_mp4(step_dir, started_wall)
    if not video:
        return (
            Check(
                name,
                FAIL,
                f"manimgl exited 0 after {secs:.1f}s but wrote no .mp4 under {step_dir}",
                "Check render.log (use --keep); ffmpeg may have failed to write.",
            ),
            secs,
        )
    try:
        info = probe_video(video)
    except ValueError as e:
        return Check(
            name, FAIL, str(e), "Check that ffprobe runs: ffprobe -version"
        ), secs
    status, detail = evaluate_probe(info, expected_height)
    return (
        Check(
            name,
            status,
            f"{detail} in {secs:.1f}s",
            "Resolution differs from what was requested; check config.yaml rendering."
            if status == WARN
            else "Output is not a valid video; check render.log (use --keep).",
        ),
        secs,
    )


def default_expected_height() -> int | None:
    try:
        _ensure_project_on_path()
        from manimgen import paths

        return int(paths.render_resolution().lower().split("x")[1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_all(args: argparse.Namespace, emit=print) -> tuple[list[Check], str]:
    """Run every step. Returns (checks, work_dir)."""
    checks: list[Check] = []

    def record(items) -> None:
        for c in items if isinstance(items, list) else [items]:
            checks.append(c)
            emit(format_check(c))

    emit("== manimgen render smoke test ==")
    emit(f"platform: {sys.platform}  python: {sys.version.split()[0]}\n")

    record(check_python())
    record(check_ffmpeg_tools())
    record(check_manimlib())
    record(check_opengl())
    record(check_latex())

    if args.out:
        work_dir = os.path.abspath(args.out)
        os.makedirs(work_dir, exist_ok=True)
    else:
        work_dir = tempfile.mkdtemp(prefix="manimgen_smoke_")

    blockers = [c.name for c in checks if c.status == FAIL]
    if blockers:
        why = "skipped because of FAIL above: " + ", ".join(blockers)
        record(Check("Render 480p", SKIP, why))
        if not args.quick:
            record(Check("Render default", SKIP, why))
        return checks, work_dir

    emit("\nRendering a ~2.5s scene at 480p (first render also warms caches)...")
    c480, _ = render_step(
        "480p",
        quality_flag="-l",
        expected_height=480,
        work_dir=work_dir,
        timeout=args.timeout,
    )
    record(c480)
    if args.quick:
        return checks, work_dir
    if c480.status == FAIL:
        record(Check("Render default", SKIP, "skipped because the 480p render failed"))
        return checks, work_dir

    emit("\nRendering at the pipeline default quality from config.yaml...")
    cdef, _ = render_step(
        "pipeline default",
        quality_flag=None,
        expected_height=default_expected_height(),
        work_dir=work_dir,
        timeout=args.timeout * 3,
    )
    record(cdef)
    return checks, work_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Check that this machine can render a manimgen video."
    )
    p.add_argument(
        "--quick", action="store_true", help="skip the pipeline-default render"
    )
    p.add_argument("--keep", action="store_true", help="keep the output folder")
    p.add_argument(
        "--out",
        metavar="DIR",
        help="write into DIR instead of a temp folder (implies --keep)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="seconds allowed for the 480p render (default 600; 1080p gets 3x)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    work_dir = None
    try:
        checks, work_dir = run_all(args, emit)
        summary = format_summary(checks)
        emit(summary)
        code = exit_code(checks)
    finally:
        keep = args.keep or bool(args.out)
        if work_dir and os.path.isdir(work_dir):
            if keep:
                try:
                    with open(
                        os.path.join(work_dir, "smoke_report.txt"),
                        "w",
                        encoding="utf-8",
                    ) as f:
                        f.write("\n".join(lines) + "\n")
                except OSError:
                    pass
                print(f"\nOutput kept in: {work_dir}")
            else:
                shutil.rmtree(work_dir, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
