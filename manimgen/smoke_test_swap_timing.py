"""
Smoke test for the swap-timing fixes on branch feature/swap-timing-fixes.

Three things changed. This test verifies each one directly:

  1. codeguard wait-clamp
     apply_known_fixes() turns self.wait(0) and self.wait(-x) into self.wait(0.01).
     Positive waits must not be touched.

  2. muxer large-mismatch warning
     mux_audio_video() emits a WARNING-level log when |video - audio| > 1.5s,
     in addition to the existing 0.5s warning. Small mismatches must not trigger it.

  3. array_swap example scene renders without crashing
     examples/array_swap_scene.py uses the correct parallel-list pattern.
     manimgl must exit 0. The rendered video must have a non-trivial duration
     (proves the animation actually ran, not just a black frame).

Run:
    cd manimgen/
    python3 smoke_test_swap_timing.py
"""

import logging
import os
import subprocess
import sys
import json
from unittest.mock import patch

sys.path.insert(0, ".")

PASS = []
FAIL = []

def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
        PASS.append(label)
    else:
        msg = f"  ✗ FAIL: {label}" + (f"\n    {detail}" if detail else "")
        print(msg)
        FAIL.append(label)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("CHECK 1: codeguard clamps zero/negative self.wait()")
print("=" * 60)

from manimgen.validator.codeguard import apply_known_fixes

cases_clamped = [
    "self.wait(0)",
    "self.wait(0.0)",
    "self.wait(-1)",
    "self.wait(-0.3)",
    "self.wait( -1.5 )",
]
for code in cases_clamped:
    fixed, applied = apply_known_fixes(code)
    check(
        f"'{code}' → self.wait(0.01)",
        "self.wait(0.01)" in fixed and code.strip() not in fixed.strip(),
        f"got: {fixed.strip()!r}",
    )

cases_untouched = [
    ("self.wait(2.7)", "2.7"),
    ("self.wait(0.5)", "0.5"),
    ("self.wait(0.01)", "0.01"),
]
for code, expected_val in cases_untouched:
    fixed, _ = apply_known_fixes(code)
    check(
        f"'{code}' not altered",
        f"self.wait({expected_val})" in fixed,
        f"got: {fixed.strip()!r}",
    )

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("CHECK 2: muxer large-mismatch warning threshold")
print("=" * 60)

from manimgen.renderer.muxer import mux_audio_video

def _fake_ffmpeg_ok(cmd, **kwargs):
    # touch the output file so mux_audio_video returns successfully
    import subprocess as _sp
    out = cmd[-1]
    open(out, "w").close()
    class R:
        returncode = 0
        stderr = ""
    return R()

import tempfile, os

with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "out.mp4")

    # 1.6s diff → must emit LARGE MISMATCH warning
    with patch("manimgen.renderer.muxer._get_duration", side_effect=[3.0, 4.6, 3.0]), \
         patch("manimgen.renderer.muxer.subprocess.run", side_effect=_fake_ffmpeg_ok):
        with patch("manimgen.renderer.muxer.logger") as mock_log:
            try:
                mux_audio_video("fake_video.mp4", "fake_audio.m4a", out)
            except Exception:
                pass
            warning_calls = [str(c) for c in mock_log.warning.call_args_list]
            large_mismatch_warned = any("LARGE MISMATCH" in c for c in warning_calls)
            check(
                "diff=1.6s triggers LARGE MISMATCH warning",
                large_mismatch_warned,
                f"warning calls: {warning_calls}",
            )

    # 0.4s diff → must NOT emit LARGE MISMATCH warning (below both thresholds)
    with patch("manimgen.renderer.muxer._get_duration", side_effect=[4.0, 4.4, 4.0]), \
         patch("manimgen.renderer.muxer.subprocess.run", side_effect=_fake_ffmpeg_ok):
        with patch("manimgen.renderer.muxer.logger") as mock_log:
            try:
                mux_audio_video("fake_video.mp4", "fake_audio.m4a", out)
            except Exception:
                pass
            warning_calls = [str(c) for c in mock_log.warning.call_args_list]
            large_mismatch_warned = any("LARGE MISMATCH" in c for c in warning_calls)
            check(
                "diff=0.4s does NOT trigger LARGE MISMATCH warning",
                not large_mismatch_warned,
                f"unexpected warning calls: {warning_calls}",
            )

    # 0.6s diff → triggers the 0.5s warning but NOT the 1.5s LARGE MISMATCH
    with patch("manimgen.renderer.muxer._get_duration", side_effect=[4.0, 4.6, 4.0]), \
         patch("manimgen.renderer.muxer.subprocess.run", side_effect=_fake_ffmpeg_ok):
        with patch("manimgen.renderer.muxer.logger") as mock_log:
            try:
                mux_audio_video("fake_video.mp4", "fake_audio.m4a", out)
            except Exception:
                pass
            warning_calls = [str(c) for c in mock_log.warning.call_args_list]
            any_warned = len(warning_calls) > 0
            large_mismatch_warned = any("LARGE MISMATCH" in c for c in warning_calls)
            check(
                "diff=0.6s triggers 0.5s warning",
                any_warned,
                f"warning calls: {warning_calls}",
            )
            check(
                "diff=0.6s does NOT trigger LARGE MISMATCH warning",
                not large_mismatch_warned,
                f"unexpected LARGE MISMATCH in: {warning_calls}",
            )

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("CHECK 3: array_swap_scene.py renders without crashing")
print("=" * 60)

scene_path = os.path.join(os.path.dirname(__file__), "examples", "array_swap_scene.py")
check("examples/array_swap_scene.py exists", os.path.exists(scene_path), scene_path)

if os.path.exists(scene_path):
    result = subprocess.run(
        ["manimgl", scene_path, "ArraySwapScene", "-w", "--hd", "-c", "#1C1C1C"],
        capture_output=True, text=True,
    )
    check(
        "manimgl render exits 0 (no crash)",
        result.returncode == 0,
        result.stderr[-600:] if result.returncode != 0 else "",
    )

    if result.returncode == 0:
        # Find the rendered file
        rendered = None
        for root, dirs, files in os.walk("videos"):
            for f in files:
                if "ArraySwapScene" in f and f.endswith(".mp4"):
                    rendered = os.path.join(root, f)
        check("rendered .mp4 exists", rendered is not None)

        if rendered:
            # Check actual duration — must be > 10s (scene has 4 cues totalling ~14.7s)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", rendered],
                capture_output=True, text=True,
            )
            if probe.returncode == 0:
                dur = float(json.loads(probe.stdout)["format"]["duration"])
                check(
                    f"rendered duration > 10s (got {dur:.1f}s) — proves animation ran, not a black frame",
                    dur > 10.0,
                    f"duration={dur:.1f}s",
                )
            else:
                check("ffprobe could read rendered file", False, probe.stderr)

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
if FAIL:
    print(f"FAILED — {len(FAIL)} check(s) failed, {len(PASS)} passed")
    for f in FAIL:
        print(f"  ✗ {f}")
    sys.exit(1)
else:
    print(f"ALL {len(PASS)} CHECKS PASSED")
print("=" * 60)
