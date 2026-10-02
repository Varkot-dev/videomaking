"""Run a child process so that a timeout stops the whole process tree.

``subprocess.run(timeout=...)`` kills only the direct child. A manimgl render
spawns children (latex, ffmpeg, and on Windows the pip launcher can sit in front
of python.exe), which would keep running, keep burning CPU and keep the output
mp4 open. The child here leads its own process group (POSIX session, Windows
``CREATE_NEW_PROCESS_GROUP``) so the whole tree can be killed: ``killpg`` on
POSIX, ``taskkill /T /F`` on Windows.
"""

from __future__ import annotations

import os
import signal
import subprocess

# How long to wait for the pipes to drain after the tree has been killed.
_DRAIN_SECONDS = 30


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and everything it started."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
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
    """Run ``cmd`` (no shell) and return ``(returncode, stdout, stderr, timed_out)``.

    Output is decoded as UTF-8 with replacement. On timeout the whole process
    tree is killed and the result is ``(None, partial_out, partial_err, True)``.
    A command that cannot be started returns ``(None, "", message, False)``
    instead of raising.
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
        kill_process_tree(proc)
        try:
            out, err = proc.communicate(timeout=_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return None, out or "", err or "", True
