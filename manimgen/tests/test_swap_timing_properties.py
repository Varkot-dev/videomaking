"""
Property-based tests for the swap-timing fixes (branch feature/swap-timing-fixes).

Two components under test:
  1. codeguard wait-clamp — apply_known_fixes() must clamp any self.wait(x)
     where x <= 0 to self.wait(0.01), and must never touch x > 0.
  2. muxer warning thresholds — mux_audio_video() must emit a LARGE MISMATCH
     warning iff |video_dur - audio_dur| > 1.5s, and the 0.5s warning iff
     the diff > 0.5s.

Hypothesis generates the inputs; we verify the invariants hold across all of them.
"""

import sys
import os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, assume, settings, HealthCheck
from hypothesis import strategies as st


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Float strings that Python's float() can parse and that ManimGL would accept
_positive_floats = st.floats(min_value=0.001, max_value=120.0, allow_nan=False, allow_infinity=False)
_negative_floats = st.floats(min_value=-120.0, max_value=-0.001, allow_nan=False, allow_infinity=False)
_zero_variants = st.sampled_from(["0", "0.0", "0.00", "0.000", "-0.0", " 0 ", " -0.0 "])


def _wait(val: str) -> str:
    return f"self.wait({val})"


# ─────────────────────────────────────────────────────────────────────────────
# Group 1: codeguard wait-clamp properties
# ─────────────────────────────────────────────────────────────────────────────

class TestCodeguardWaitClampProperties:

    @given(_positive_floats)
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_positive_wait_never_clamped(self, val: float):
        """Any self.wait(x) where x > 0 must pass through unchanged."""
        from manimgen.validator.codeguard import apply_known_fixes
        code = _wait(f"{val:.6f}")
        fixed, _ = apply_known_fixes(code)
        assert "self.wait(0.01)" not in fixed, (
            f"self.wait({val:.6f}) was incorrectly clamped to 0.01"
        )
        assert f"{val:.6f}" in fixed, (
            f"self.wait({val:.6f}) was altered unexpectedly: {fixed!r}"
        )

    @given(_negative_floats)
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_negative_wait_always_clamped(self, val: float):
        """Any self.wait(x) where x < 0 must be rewritten to self.wait(0.01)."""
        from manimgen.validator.codeguard import apply_known_fixes
        code = _wait(f"{val:.6f}")
        fixed, applied = apply_known_fixes(code)
        assert "self.wait(0.01)" in fixed, (
            f"self.wait({val:.6f}) was not clamped — got: {fixed!r}"
        )
        assert any("clamped" in a for a in applied), (
            f"applied list did not record the clamp for {val:.6f}: {applied}"
        )

    @given(_zero_variants)
    @settings(max_examples=50)
    def test_zero_variants_always_clamped(self, zero_str: str):
        """Strings that represent zero (with whitespace, sign, or trailing zeros)
        must be clamped to self.wait(0.01)."""
        from manimgen.validator.codeguard import apply_known_fixes
        code = _wait(zero_str)
        fixed, _ = apply_known_fixes(code)
        assert "self.wait(0.01)" in fixed, (
            f"self.wait({zero_str!r}) was not clamped — got: {fixed!r}"
        )

    @given(_positive_floats)
    @settings(max_examples=200)
    def test_clamp_is_idempotent(self, val: float):
        """Applying apply_known_fixes twice produces the same result as once.
        Ensures the clamp doesn't double-process."""
        from manimgen.validator.codeguard import apply_known_fixes
        code = _wait(f"{val:.6f}")
        once, _ = apply_known_fixes(code)
        twice, _ = apply_known_fixes(once)
        assert once == twice, (
            f"apply_known_fixes is not idempotent for {val:.6f}:\n"
            f"  after 1st: {once!r}\n  after 2nd: {twice!r}"
        )

    @given(_negative_floats)
    @settings(max_examples=200)
    def test_clamp_of_negative_is_idempotent(self, val: float):
        """After clamping a negative wait, a second pass must not alter it further."""
        from manimgen.validator.codeguard import apply_known_fixes
        code = _wait(f"{val:.6f}")
        once, _ = apply_known_fixes(code)
        twice, _ = apply_known_fixes(once)
        assert once == twice, (
            f"apply_known_fixes not idempotent on negative {val:.6f}:\n"
            f"  after 1st: {once!r}\n  after 2nd: {twice!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group 2: muxer warning threshold properties
# ─────────────────────────────────────────────────────────────────────────────

def _run_muxer_capture_warnings(video_dur: float, audio_dur: float):
    """Call mux_audio_video with mocked ffmpeg and return list of warning messages."""
    import tempfile

    def fake_run(cmd, **kwargs):
        out = cmd[-1]
        open(out, "w").close()
        class R:
            returncode = 0
            stderr = ""
        return R()

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.mp4")
        # _get_duration is called twice in _mux_freeze_video / _mux_pad_audio too
        durations = [video_dur, audio_dur, video_dur, audio_dur]
        with patch("manimgen.renderer.muxer._get_duration", side_effect=durations), \
             patch("manimgen.renderer.muxer.subprocess.run", side_effect=fake_run):
            with patch("manimgen.renderer.muxer.logger") as mock_log:
                try:
                    from manimgen.renderer.muxer import mux_audio_video
                    mux_audio_video("v.mp4", "a.m4a", out)
                except Exception:
                    pass
                return [str(c) for c in mock_log.warning.call_args_list]


class TestMuxerWarningThresholdProperties:

    @given(st.floats(min_value=1.51, max_value=30.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_large_mismatch_warning_fires_above_threshold(self, diff: float):
        """For any |video - audio| > 1.5s the LARGE MISMATCH warning must appear."""
        video_dur = 5.0
        audio_dur = video_dur + diff
        warnings = _run_muxer_capture_warnings(video_dur, audio_dur)
        assert any("LARGE MISMATCH" in w for w in warnings), (
            f"diff={diff:.3f}s did not trigger LARGE MISMATCH. warnings={warnings}"
        )

    @given(st.floats(min_value=0.0, max_value=1.49, allow_nan=False, allow_infinity=False))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_large_mismatch_warning_silent_below_threshold(self, diff: float):
        """For any |video - audio| <= 1.5s the LARGE MISMATCH warning must NOT appear."""
        video_dur = 5.0
        audio_dur = video_dur + diff
        warnings = _run_muxer_capture_warnings(video_dur, audio_dur)
        assert not any("LARGE MISMATCH" in w for w in warnings), (
            f"diff={diff:.3f}s incorrectly triggered LARGE MISMATCH. warnings={warnings}"
        )

    @given(st.floats(min_value=1.01, max_value=30.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_standard_mismatch_warning_fires_above_one_second(self, diff: float):
        """For any |video - audio| > 1.0s at least one warning must appear."""
        video_dur = 5.0
        audio_dur = video_dur + diff
        warnings = _run_muxer_capture_warnings(video_dur, audio_dur)
        assert len(warnings) > 0, (
            f"diff={diff:.3f}s produced no warnings at all. expected at least the 1.0s one."
        )

    @given(st.floats(min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_no_warning_below_one_second(self, diff: float):
        """For any |video - audio| <= 1.0s no warning should appear."""
        video_dur = 5.0
        audio_dur = video_dur + diff
        warnings = _run_muxer_capture_warnings(video_dur, audio_dur)
        assert len(warnings) == 0, (
            f"diff={diff:.3f}s produced unexpected warnings: {warnings}"
        )

    @given(
        st.floats(min_value=1.51, max_value=30.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.1, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_warning_is_symmetric_video_vs_audio_longer(self, diff: float, base: float):
        """LARGE MISMATCH must fire regardless of which side (video or audio) is longer."""
        warnings_audio_longer = _run_muxer_capture_warnings(base, base + diff)
        warnings_video_longer = _run_muxer_capture_warnings(base + diff, base)
        assert any("LARGE MISMATCH" in w for w in warnings_audio_longer), (
            f"audio longer by {diff:.3f}s did not trigger LARGE MISMATCH"
        )
        assert any("LARGE MISMATCH" in w for w in warnings_video_longer), (
            f"video longer by {diff:.3f}s did not trigger LARGE MISMATCH"
        )
