"""utils.load_reference_frames: one unreadable PNG must not break the rest."""

import base64

from manimgen import utils


def test_unreadable_frame_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    good = tmp_path / "a_good.png"
    good.write_bytes(b"\x89PNG fake")
    missing = tmp_path / "b_missing.png"  # listed, then gone: open() raises

    monkeypatch.setattr(utils.glob, "glob", lambda pattern: [str(missing), str(good)])
    with caplog.at_level("WARNING", logger="manimgen.utils"):
        frames = utils.load_reference_frames()

    assert frames == [base64.b64encode(b"\x89PNG fake").decode("utf-8")]
    assert "b_missing.png" in caplog.text
