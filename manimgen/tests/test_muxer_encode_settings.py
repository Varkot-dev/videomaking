"""Muxer encode settings: freeze-path quality, one-frame tolerance, branch
selection and a config-driven placeholder clip (#93). ffmpeg is simulated."""

from unittest.mock import MagicMock, patch

from manimgen import paths
from manimgen.renderer import muxer


def _ok():
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    m.stdout = ""
    return m


def _writing_fake(cmds=None):
    """ffmpeg stand-in that succeeds and writes the output."""

    def fake(cmd, **kwargs):
        if cmds is not None:
            cmds.append(cmd)
        if cmd and cmd[0] == "ffmpeg":
            with open(cmd[-1], "wb") as f:
                f.write(b"GOOD")
        return _ok()

    return fake


def _mux_patches(video_dur, audio_dur):
    return (
        patch.object(
            muxer, "_get_duration", side_effect=[video_dur, audio_dur, video_dur]
        ),
        patch.object(muxer, "_has_video_stream", return_value=True),
    )


class TestEncodeSettings:
    def _mux_cmd(self, tmp_path, video_dur, audio_dur):
        cmds = []
        p1, p2 = _mux_patches(video_dur, audio_dur)
        with (
            p1,
            p2,
            patch.object(muxer.subprocess, "run", side_effect=_writing_fake(cmds)),
        ):
            muxer.mux_audio_video("v.mp4", "a.m4a", str(tmp_path / "o.mp4"))
        return cmds[0]

    def test_freeze_path_has_quality_flags(self, tmp_path):
        cmd = self._mux_cmd(tmp_path, 3.0, 5.0)
        assert any("tpad" in a for a in cmd)
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert "-preset" in cmd

    def test_sub_frame_gap_takes_the_copy_branch(self, tmp_path):
        fps = paths.render_fps()
        cmd = self._mux_cmd(tmp_path, 3.0, 3.0 + 0.5 / fps)
        assert not any("tpad" in a for a in cmd)
        assert cmd[cmd.index("-c:v") + 1] == "copy"

    def test_gap_over_one_frame_freezes(self, tmp_path):
        fps = paths.render_fps()
        cmd = self._mux_cmd(tmp_path, 3.0, 3.0 + 2.0 / fps)
        assert any("tpad" in a for a in cmd)

    def test_placeholder_uses_config_resolution_and_fps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "render_resolution", lambda: "1280x720")
        monkeypatch.setattr(paths, "render_fps", lambda: 24)
        cmds = []
        with (
            patch.object(muxer, "_get_duration", side_effect=[1.0, 2.0]),
            patch.object(muxer, "_has_video_stream", return_value=False),
            patch.object(muxer.subprocess, "run", side_effect=_writing_fake(cmds)),
        ):
            muxer.mux_audio_video("v.mp4", "a.m4a", str(tmp_path / "o.mp4"))
        src = cmds[0][cmds[0].index("-i") + 1]
        assert "s=1280x720" in src and "r=24" in src
        assert cmds[0][cmds[0].index("-pix_fmt") + 1] == "yuv420p"
