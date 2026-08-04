"""Regression tests for B3 — `_has_audio_stream` leaked processes and
inverted its own documented fail-safe.

Two defects in one small function, which is called once per clip:

  1. **Process leak.** `Popen` + `communicate(timeout=10)` never `kill()`ed and
     reaped the child on `TimeoutExpired`. The ffprobe process is left running
     and becomes a zombie once the parent moves on. Once per clip, this
     accumulates across a whole assembly.

  2. **Inverted fail-safe.** On a non-zero ffprobe exit the function returned
     `True` ("this clip has audio"), while the docstring immediately below
     argues at length that `False` is correct: the no-audio branch in
     `_normalise_all` injects a silent `anullsrc` track and always yields a
     valid `[v][a]` clip, whereas wrongly assuming audio makes ffmpeg's
     `-c:a aac` map fail outright and abort assembly. The exception handler
     already returned `False`; only the returncode branch disagreed.

The docstring is right and the code was wrong, so the fix follows the
docstring: both failure paths now return `False`.
"""

import subprocess
from unittest.mock import MagicMock, patch

from manimgen.renderer.assembler import _has_audio_stream


def _proc(returncode: int, stdout: str = "", timeout: bool = False) -> MagicMock:
    """A Popen stub that models real poll()/communicate() semantics.

    Crucially, a timed-out child is still RUNNING: poll() returns None until
    something kills it. Getting this right is what makes the leak observable.
    """
    p = MagicMock()
    p.returncode = returncode

    if timeout:
        # First communicate() times out; a later one (after kill) succeeds.
        p.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="ffprobe", timeout=10),
            ("", ""),
        ]
        state = {"alive": True}

        def _kill():
            state["alive"] = False

        p.kill.side_effect = _kill
        p.poll.side_effect = lambda: None if state["alive"] else -9
    else:
        p.communicate.return_value = (stdout, "")
        p.poll.return_value = returncode

    return p


# ---------------------------------------------------------------------------
# Defect 1 — the child process must be reaped
# ---------------------------------------------------------------------------


def test_timed_out_probe_is_killed_and_reaped():
    """A hung ffprobe must be killed AND waited on, not leaked as a zombie."""
    p = _proc(returncode=0, timeout=True)

    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        _has_audio_stream("/tmp/clip.mp4")

    assert p.kill.called, (
        "hung ffprobe child was never killed — one leaked process per clip"
    )
    # kill() alone leaves a zombie; the parent must also reap it.
    assert p.wait.called or p.communicate.call_count > 1, (
        "killed child was never reaped — it stays a zombie until the parent exits"
    )


def test_successful_probe_does_not_kill():
    """The happy path must not kill a process that already exited cleanly."""
    p = _proc(returncode=0, stdout="audio\n")

    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        assert _has_audio_stream("/tmp/clip.mp4") is True

    assert not p.kill.called


def test_child_is_reaped_even_when_probe_fails():
    """Any exit path reaps the child."""
    p = _proc(returncode=1)

    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        _has_audio_stream("/tmp/clip.mp4")

    # Either communicate() completed (child reaped) or it was explicitly killed.
    assert p.communicate.called


# ---------------------------------------------------------------------------
# Defect 2 — the fail-safe must match the documented reasoning
# ---------------------------------------------------------------------------


def test_nonzero_returncode_fails_safe_to_no_audio():
    """A failed probe must report NO audio, per the function's own docstring.

    Returning True here makes _normalise_all pick the `-c:a aac` branch with
    no audio stream to encode, which aborts the whole assembly. Returning
    False routes to the anullsrc branch, which always succeeds.
    """
    p = _proc(returncode=1, stdout="")

    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        result = _has_audio_stream("/tmp/clip.mp4")

    assert result is False, (
        "probe failure returned True (assume audio present) — this contradicts "
        "the docstring and makes ffmpeg's -c:a aac map fail, aborting assembly"
    )


def test_timeout_fails_safe_to_no_audio():
    p = _proc(returncode=0, timeout=True)

    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        assert _has_audio_stream("/tmp/clip.mp4") is False


def test_popen_raising_fails_safe_to_no_audio():
    """Pre-existing behaviour: exceptions already failed safe. Keep it."""
    with patch(
        "manimgen.renderer.assembler.subprocess.Popen",
        side_effect=OSError("ffprobe missing"),
    ):
        assert _has_audio_stream("/tmp/clip.mp4") is False


def test_all_failure_modes_agree():
    """Every failure mode must fail-safe the SAME way — no split-brain.

    The original bug was exactly this disagreement: the exception handler
    returned False while the returncode branch returned True.
    """
    results = []

    p_rc = _proc(returncode=1)
    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p_rc):
        results.append(_has_audio_stream("/tmp/c.mp4"))

    p_to = _proc(returncode=0, timeout=True)
    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p_to):
        results.append(_has_audio_stream("/tmp/c.mp4"))

    with patch(
        "manimgen.renderer.assembler.subprocess.Popen", side_effect=OSError("boom")
    ):
        results.append(_has_audio_stream("/tmp/c.mp4"))

    assert results == [False, False, False], (
        f"failure modes disagree on the fail-safe direction: {results}"
    )


# ---------------------------------------------------------------------------
# Happy paths still work
# ---------------------------------------------------------------------------


def test_audio_present_returns_true():
    p = _proc(returncode=0, stdout="audio\n")
    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        assert _has_audio_stream("/tmp/clip.mp4") is True


def test_no_audio_stream_returns_false():
    p = _proc(returncode=0, stdout="   \n")
    with patch("manimgen.renderer.assembler.subprocess.Popen", return_value=p):
        assert _has_audio_stream("/tmp/clip.mp4") is False
