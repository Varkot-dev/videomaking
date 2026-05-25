"""Tests for the shared manimgl render-command builder.

Guards against the --fps int/str crash (2026-05-25): manimgl 1.7.2's --fps
argparse arg has no type=int, so config.py assigns the raw string into
camera_config.fps and Scene.run() crashes on `1 / self.camera.fps`
(TypeError: int / str). The flag is irreparably broken in this build, so the
render command must not emit it. A single builder keeps runner/fallback/retry
from drifting apart on this.
"""

from __future__ import annotations

from manimgen.validator.render_command import build_manimgl_command


class TestBuildManimglCommand:
    def test_includes_core_flags(self):
        cmd = build_manimgl_command("scene.py", "MyScene")
        assert cmd[0] == "manimgl"
        assert "scene.py" in cmd
        assert "MyScene" in cmd
        assert "-w" in cmd  # write to file
        # dark background flag with the canonical color
        assert "-c" in cmd
        assert "#1C1C1C" in cmd

    def test_does_not_pass_broken_fps_flag(self):
        """--fps crashes manimgl 1.7.2 (no int cast). It must never be emitted."""
        cmd = build_manimgl_command("scene.py", "MyScene")
        assert "--fps" not in cmd, (
            "--fps is irreparably broken in manimgl 1.7.2 (int/str crash) — "
            "the assembler normalizes fps at the end instead."
        )

    def test_quality_flag_present(self):
        cmd = build_manimgl_command("scene.py", "MyScene")
        # render_quality_flag() returns e.g. '--hd'
        assert any(part.startswith("--") and part not in ("--fps",) for part in cmd)
