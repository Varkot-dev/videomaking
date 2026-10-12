"""Atomic writes for muxed clips, cuts and the final video (#76), and the
muxer encode settings (#93).

ffmpeg is simulated: the fake writes a truncated file to the output path (the
last argv entry) the way a killed encoder would, then fails or times out.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from manimgen import paths
from manimgen.renderer import assembler, muxer
from manimgen.utils import atomic_output


def _ok():
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    m.stdout = ""
    return m


def _truncating_fake(exc=None, returncode=1):
    """ffmpeg stand-in: leaves a partial output, then fails."""

    def fake(cmd, **kwargs):
        with open(cmd[-1], "wb") as f:
            f.write(b"TRUNCATED")
        if exc is not None:
            raise exc
        m = MagicMock()
        m.returncode = returncode
        m.stderr = "boom"
        return m

    return fake


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


class TestMuxAtomic:
    @pytest.mark.parametrize(
        "fake",
        [
            _truncating_fake(returncode=1),
            _truncating_fake(exc=subprocess.TimeoutExpired("ffmpeg", 300)),
        ],
        ids=["nonzero-exit", "timeout"],
    )
    @pytest.mark.parametrize("durs", [(5.0, 3.0), (3.0, 5.0)], ids=["pad", "freeze"])
    def test_failed_mux_leaves_nothing(self, tmp_path, fake, durs):
        out = tmp_path / "out" / "section_01_cue00.mp4"
        p1, p2 = _mux_patches(*durs)
        with p1, p2, patch.object(muxer.subprocess, "run", side_effect=fake):
            with pytest.raises(RuntimeError):
                muxer.mux_audio_video("v.mp4", "a.m4a", str(out))
        assert not out.exists()
        assert os.listdir(out.parent) == []

    def test_failed_mux_keeps_previous_good_file(self, tmp_path):
        out = tmp_path / "clip.mp4"
        out.write_bytes(b"PREVIOUS")
        p1, p2 = _mux_patches(3.4, 3.0)
        with (
            p1,
            p2,
            patch.object(muxer.subprocess, "run", side_effect=_truncating_fake()),
        ):
            with pytest.raises(RuntimeError):
                muxer.mux_audio_video("v.mp4", "a.m4a", str(out))
        assert out.read_bytes() == b"PREVIOUS"
        assert os.listdir(tmp_path) == ["clip.mp4"]

    def test_successful_mux_replaces_existing_file(self, tmp_path):
        out = tmp_path / "clip.mp4"
        out.write_bytes(b"OLD")
        cmds = []
        p1, p2 = _mux_patches(3.4, 3.0)
        with (
            p1,
            p2,
            patch.object(muxer.subprocess, "run", side_effect=_writing_fake(cmds)),
        ):
            muxer.mux_audio_video("v.mp4", "a.m4a", str(out))
        assert out.read_bytes() == b"GOOD"
        assert os.listdir(tmp_path) == ["clip.mp4"]
        # ffmpeg wrote to a temp name, never straight to the final path
        assert cmds[0][-1] != str(out)
        assert cmds[0][-1].endswith(".mp4")

    def test_exit_zero_without_output_is_an_error(self, tmp_path):
        out = tmp_path / "clip.mp4"
        p1, p2 = _mux_patches(5.0, 3.0)
        with p1, p2, patch.object(muxer.subprocess, "run", return_value=_ok()):
            with pytest.raises(RuntimeError):
                muxer.mux_audio_video("v.mp4", "a.m4a", str(out))
        assert not out.exists()


class TestCutAtomic:
    @pytest.mark.parametrize(
        "fake",
        [
            _truncating_fake(returncode=1),
            _truncating_fake(exc=subprocess.TimeoutExpired("ffmpeg", 300)),
        ],
        ids=["nonzero-exit", "timeout"],
    )
    def test_failed_cut_leaves_nothing(self, tmp_path, fake):
        out_dir = tmp_path / "muxed"
        with patch.object(muxer.subprocess, "run", side_effect=fake):
            with pytest.raises(RuntimeError):
                muxer.cut_video_at_cues("v.mp4", [0.0], [1.0], str(out_dir), "s1")
        assert os.listdir(out_dir) == []

    def test_successful_cut_lands_at_final_name_only(self, tmp_path):
        out_dir = tmp_path / "muxed"
        with patch.object(muxer.subprocess, "run", side_effect=_writing_fake()):
            res = muxer.cut_video_at_cues(
                "v.mp4", [0.0, 1.0], [1.0, 1.0], str(out_dir), "s1"
            )
        assert sorted(os.listdir(out_dir)) == sorted(os.path.basename(p) for p in res)


class TestAtomicOutputHelper:
    def test_temp_names_do_not_collide(self, tmp_path):
        final = str(tmp_path / "x.mp4")
        with atomic_output(final) as a, atomic_output(final) as b:
            for t in (a, b):
                with open(t, "wb") as f:
                    f.write(b"x")
            assert a != b
            assert a != final and b != final
            assert a.endswith(".mp4") and b.endswith(".mp4")

    def test_replace_over_existing_file(self, tmp_path):
        final = tmp_path / "x.mp4"
        final.write_bytes(b"OLD")
        with atomic_output(str(final)) as tmp:
            with open(tmp, "wb") as f:
                f.write(b"NEW")
        assert final.read_bytes() == b"NEW"
        assert os.listdir(tmp_path) == ["x.mp4"]

    def test_replace_retries_on_permission_error(self, tmp_path):
        final = tmp_path / "x.mp4"
        real = os.replace
        attempts = []

        def flaky(src, dst):
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError("in use")
            real(src, dst)

        with (
            patch("manimgen.utils.os.replace", side_effect=flaky),
            patch("manimgen.utils.time.sleep"),
        ):
            with atomic_output(str(final)) as tmp:
                with open(tmp, "wb") as f:
                    f.write(b"NEW")
        assert len(attempts) == 3
        assert final.read_bytes() == b"NEW"

    def test_persistent_permission_error_cleans_temp(self, tmp_path):
        final = tmp_path / "x.mp4"
        with (
            patch("manimgen.utils.os.replace", side_effect=PermissionError),
            patch("manimgen.utils.time.sleep"),
        ):
            with pytest.raises(PermissionError):
                with atomic_output(str(final)) as tmp:
                    with open(tmp, "wb") as f:
                        f.write(b"NEW")
        assert os.listdir(tmp_path) == []

    def test_exception_in_body_removes_temp_and_keeps_old(self, tmp_path):
        final = tmp_path / "x.mp4"
        final.write_bytes(b"OLD")
        with pytest.raises(ValueError):
            with atomic_output(str(final)) as tmp:
                with open(tmp, "wb") as f:
                    f.write(b"half")
                raise ValueError("x")
        assert final.read_bytes() == b"OLD"
        assert os.listdir(tmp_path) == ["x.mp4"]


@pytest.fixture
def vids(tmp_path, monkeypatch):
    d = tmp_path / "videos"
    d.mkdir()
    monkeypatch.setattr(paths, "videos_dir", lambda: str(d))
    return d


def _clips(tmp_path, names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"clip")
        out.append(str(p))
    return out


class TestAssembleCleanup:
    def test_failure_leaves_no_intermediates(self, tmp_path, vids):
        clips = _clips(tmp_path, ["section_01_cue00.mp4", "section_02_cue00.mp4"])
        state = {"n": 0}

        def fake(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                state["n"] += 1
                if state["n"] == 2:
                    with open(cmd[-1], "wb") as f:
                        f.write(b"half")
                    raise subprocess.CalledProcessError(1, cmd)
                with open(cmd[-1], "wb") as f:
                    f.write(b"norm")
            return _ok()

        with (
            patch.object(assembler, "_has_audio_stream", return_value=True),
            patch.object(assembler.subprocess, "run", side_effect=fake),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                assembler.assemble_video(clips, "T")
        assert os.listdir(vids) == []

    def test_success_leaves_only_final(self, tmp_path, vids):
        clips = _clips(tmp_path, ["section_01_cue00.mp4", "section_02_cue00.mp4"])
        with (
            patch.object(assembler, "_has_audio_stream", return_value=True),
            patch.object(assembler, "_video_duration", return_value=5.0),
            patch.object(assembler.subprocess, "run", side_effect=_writing_fake()),
        ):
            out = assembler.assemble_video(clips, "My Title")
        assert os.listdir(vids) == ["my_title.mp4"]
        assert out == str(vids / "my_title.mp4")

    def test_single_clip_is_copied_not_moved(self, tmp_path, vids):
        clips = _clips(tmp_path, ["section_01_cue00.mp4"])
        out = assembler.assemble_video(clips, "T")
        assert os.path.exists(clips[0])
        with open(out, "rb") as f:
            assert f.read() == b"clip"
        assert os.listdir(vids) == ["t.mp4"]

    def test_locked_final_falls_back_to_timestamped_name(self, tmp_path, vids):
        clips = _clips(tmp_path, ["section_01_cue00.mp4"])
        (vids / "t.mp4").write_bytes(b"OPEN IN PLAYER")
        real = os.replace

        def locked(src, dst):
            if os.path.basename(dst) == "t.mp4":
                raise PermissionError("locked")
            real(src, dst)

        with (
            patch("manimgen.utils.os.replace", side_effect=locked),
            patch("manimgen.utils.time.sleep"),
        ):
            out = assembler.assemble_video(clips, "T")
        assert os.path.basename(out) != "t.mp4"
        assert os.path.basename(out).startswith("t_")
        with open(out, "rb") as f:
            assert f.read() == b"clip"
        assert (vids / "t.mp4").read_bytes() == b"OPEN IN PLAYER"
        assert sorted(os.listdir(vids)) == sorted(["t.mp4", os.path.basename(out)])


class TestCrossfadeTimeout:
    def test_timeout_is_a_clear_error_and_cleans_up(self, tmp_path, vids):
        clips = _clips(tmp_path, ["section_01_cue00.mp4", "section_02_cue00.mp4"])

        def fake(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                if "-filter_complex" in cmd:
                    raise subprocess.TimeoutExpired(cmd, 300)
                with open(cmd[-1], "wb") as f:
                    f.write(b"norm")
            return _ok()

        with (
            patch.object(assembler, "_has_audio_stream", return_value=True),
            patch.object(assembler, "_video_duration", return_value=5.0),
            patch.object(assembler.subprocess, "run", side_effect=fake),
        ):
            with pytest.raises(RuntimeError) as ei:
                assembler.assemble_video(clips, "T")
        msg = str(ei.value)
        assert "300" in msg and "crossfade" in msg.lower()
        assert os.listdir(vids) == []
