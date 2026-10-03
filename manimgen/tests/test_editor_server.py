"""
Editor server hardening tests (#39).

Covers the four MEDIUM hardening fixes plus the LOW probe-duration change.
ffprobe/ffmpeg subprocesses are mocked — ZERO real subprocess, ZERO network.
The XSS fix itself is in the template (clip.id is rendered via textContent /
programmatic DOM, never innerHTML); a static check below asserts the unsafe
sink is gone.
"""

import json
from pathlib import Path

import pytest

from manimgen.editor import server


@pytest.fixture
def client(mocker, tmp_path):
    """Flask test client with VIDEOS_DIR pointed at a tmp dir and same-origin auth.

    The before_request guard allows same-origin POSTs; the test client sets a
    matching Origin header so mutating endpoints are reachable without a token.
    """
    server.VIDEOS_DIR = Path(tmp_path)
    server.EDITOR_TOKEN = ""
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _same_origin_headers(client):
    # request.host_url for the test client is http://localhost/
    return {"Origin": "http://localhost", "Content-Type": "application/json"}


# ── _probe_duration: surfaces failure as None, no fabricated 0.0 ─────────────


@pytest.mark.unit
def test_probe_duration_parses_valid_ffprobe_json(mocker, tmp_path):
    mocker.patch(
        "manimgen.editor.server.subprocess.run",
        return_value=mocker.MagicMock(
            stdout=json.dumps({"format": {"duration": "12.345"}})
        ),
    )
    assert server._probe_duration(tmp_path / "a.mp4") == 12.35


@pytest.mark.unit
def test_probe_duration_returns_none_on_na_duration(mocker, tmp_path):
    """ffprobe emits "N/A" for some containers — must surface as None, not 0.0."""
    mocker.patch(
        "manimgen.editor.server.subprocess.run",
        return_value=mocker.MagicMock(
            stdout=json.dumps({"format": {"duration": "N/A"}})
        ),
    )
    assert server._probe_duration(tmp_path / "a.mp4") is None


@pytest.mark.unit
def test_probe_duration_returns_none_when_ffprobe_missing(mocker, tmp_path):
    """ffprobe not installed → OSError → None (logged), never a crash or 0.0."""
    mocker.patch(
        "manimgen.editor.server.subprocess.run",
        side_effect=FileNotFoundError("ffprobe"),
    )
    assert server._probe_duration(tmp_path / "a.mp4") is None


@pytest.mark.unit
def test_probe_duration_returns_none_on_unparseable_output(mocker, tmp_path):
    mocker.patch(
        "manimgen.editor.server.subprocess.run",
        return_value=mocker.MagicMock(stdout="not json"),
    )
    assert server._probe_duration(tmp_path / "a.mp4") is None


# ── api_export: request.json None guard ─────────────────────────────────────


@pytest.mark.security
def test_export_rejects_empty_body_with_400(client):
    """Wrong/empty Content-Type previously made request.json None → 500.

    With the fix it is a clean 400, not an AttributeError 500.
    """
    resp = client.post("/api/export", data="", headers={"Origin": "http://localhost"})
    assert resp.status_code == 400
    assert "JSON object" in resp.get_json()["error"]


@pytest.mark.security
def test_export_rejects_json_array_body_with_400(client):
    """A JSON array (not an object) must be a 400, not a crash."""
    resp = client.post(
        "/api/export",
        data=json.dumps([1, 2, 3]),
        headers=_same_origin_headers(client),
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_export_rejects_no_clips_with_400(client):
    resp = client.post(
        "/api/export",
        data=json.dumps({"title": "x", "clips": []}),
        headers=_same_origin_headers(client),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No clips provided"


# ── safe_title: length cap, null-byte, backslash, traversal neutralized ──────


@pytest.mark.security
@pytest.mark.parametrize(
    "raw_title,expected",
    [
        ("My Video", "My_Video"),
        # Traversal is neutralized: every "." and "/" becomes "_".
        ("../../etc/passwd", "______etc_passwd"),
        ("a\\b/c", "a_b_c"),
        # Null byte slugged → no truncation-attack surface.
        ("name\x00.mp4", "name__mp4"),
        # Only an EMPTY slug falls back to the default (spec: [:120] or default).
        ("", "final_video"),
        ("///", "___"),
        # Length is capped at 120.
        ("a" * 500, "a" * 120),
    ],
)
def test_safe_title_sanitization(client, mocker, raw_title, expected, tmp_path):
    """safe_title slugs everything outside [A-Za-z0-9_-], caps at 120, defaults.

    We intercept the export at the ffmpeg trim step by giving one (missing)
    clip, which returns 400 before any subprocess — but the safe_title is
    computed first and used for the output path. We assert via the exports
    dir name by driving a successful concat with mocked ffmpeg.
    """
    # Create one real source clip so the trim/concat path runs.
    src = tmp_path / "clip.mp4"
    src.write_text("video", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        # Touch every ffmpeg output so the export can publish it.
        if cmd and cmd[0] == "ffmpeg":
            out = cmd[-1]
            if out.endswith(".mp4"):
                # touch outputs so finally-cleanup and concat see files
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text("out", encoding="utf-8")
        return mocker.MagicMock(returncode=0, stdout="", stderr="")

    mocker.patch("manimgen.editor.server.subprocess.run", side_effect=fake_run)

    resp = client.post(
        "/api/export",
        data=json.dumps(
            {
                "title": raw_title,
                "clips": [{"filename": "clip.mp4", "duration": 1.0}],
            }
        ),
        headers=_same_origin_headers(client),
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["filename"] == f"{expected}.mp4"
    assert (tmp_path / "exports" / f"{expected}.mp4").read_text(
        encoding="utf-8"
    ) == "out"


# ── XSS: the template no longer interpolates clip.id into markup ─────────────

_EDITOR_HTML = (Path(server.__file__).parent / "templates" / "editor.html").read_text(
    encoding="utf-8"
)


@pytest.mark.security
def test_editor_template_does_not_interpolate_clip_id_into_innerhtml():
    """clip.id is filename-derived (untrusted) — it must reach the DOM only via
    textContent, never via an innerHTML template literal."""
    # The old XSS sink.
    assert "${clip.id}" not in _EDITOR_HTML
    # clip.id is now set through the escaping textContent path.
    assert "name.textContent = clip.id" in _EDITOR_HTML


@pytest.mark.security
def test_editor_template_encodes_filename_in_video_src():
    """video.src must encode the untrusted filename path segment."""
    assert "encodeURIComponent(clip.filename)" in _EDITOR_HTML
    assert "/api/video/${clip.filename}`" not in _EDITOR_HTML


# ── #95: export never overwrites, validates input, checks Host, caches probes ─


def _fake_ffmpeg(mocker, calls=None, fail_concat=False):
    """Mock subprocess.run: ffmpeg writes its output file, concat can fail."""

    def fake_run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if cmd and cmd[0] == "ffmpeg":
            if fail_concat and "concat" in cmd:
                Path(cmd[-1]).write_text("truncated", encoding="utf-8")
                return mocker.MagicMock(returncode=1, stdout="", stderr="boom")
            Path(cmd[-1]).write_text("out", encoding="utf-8")
        return mocker.MagicMock(returncode=0, stdout="", stderr="")

    mocker.patch("manimgen.editor.server.subprocess.run", side_effect=fake_run)


def _export(client, clips, title="final_video"):
    return client.post(
        "/api/export",
        data=json.dumps({"title": title, "clips": clips}),
        headers=_same_origin_headers(client),
    )


@pytest.mark.unit
def test_same_title_export_is_auto_suffixed_never_overwritten(client, mocker, tmp_path):
    (tmp_path / "clip.mp4").write_text("video", encoding="utf-8")
    _fake_ffmpeg(mocker)
    clips = [{"filename": "clip.mp4", "duration": 1.0}]

    names = [_export(client, clips).get_json()["filename"] for _ in range(3)]

    assert names == ["final_video.mp4", "final_video_2.mp4", "final_video_3.mp4"]
    for name in names:
        assert (tmp_path / "exports" / name).read_text(encoding="utf-8") == "out"


@pytest.mark.unit
def test_export_keeps_existing_file_untouched(client, mocker, tmp_path):
    (tmp_path / "clip.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "final_video.mp4").write_text("precious", encoding="utf-8")
    _fake_ffmpeg(mocker)

    resp = _export(client, [{"filename": "clip.mp4", "duration": 1.0}])

    assert resp.get_json()["filename"] == "final_video_2.mp4"
    kept = (tmp_path / "exports" / "final_video.mp4").read_text(encoding="utf-8")
    assert kept == "precious"


@pytest.mark.unit
def test_failed_export_leaves_no_partial_or_placeholder(client, mocker, tmp_path):
    (tmp_path / "clip.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "final_video.mp4").write_text("good", encoding="utf-8")
    _fake_ffmpeg(mocker, fail_concat=True)

    resp = _export(client, [{"filename": "clip.mp4", "duration": 1.0}])

    assert resp.status_code == 500
    left = sorted(p.name for p in (tmp_path / "exports").iterdir())
    assert left == ["final_video.mp4"]
    kept = (tmp_path / "exports" / "final_video.mp4").read_text(encoding="utf-8")
    assert kept == "good"
    assert not list(tmp_path.glob("_tmp_*"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        {"trim_start": "abc"},
        {"trim_end": "abc"},
        {"duration": "abc"},
        {"trim_start": "nan"},
        {"trim_end": "inf"},
        {"duration": "-inf"},
        {"trim_start": -1},
        {"trim_end": -2},
        {"duration": -3},
        {"trim_start": None},
        {"trim_start": True},
        {"trim_start": [1]},
        {"trim_start": 5, "trim_end": 2},
        {"trim_start": 2, "trim_end": 2},
        {"trim_start": 3, "duration": 1},
    ],
)
def test_export_rejects_bad_numbers_with_json_400(client, mocker, tmp_path, bad):
    (tmp_path / "clip.mp4").write_text("video", encoding="utf-8")
    run = mocker.patch("manimgen.editor.server.subprocess.run")

    resp = _export(client, [{"filename": "clip.mp4", **bad}])

    assert resp.status_code == 400
    assert resp.is_json and resp.get_json()["error"]
    run.assert_not_called()
    assert not (tmp_path / "exports").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "clips", ["clip.mp4", ["clip.mp4"], [{"trim_start": 0}], [{"filename": 3}]]
)
def test_export_rejects_malformed_clip_entries(client, clips):
    resp = _export(client, clips)
    assert resp.status_code == 400
    assert resp.is_json


@pytest.mark.unit
def test_export_without_duration_or_trim_end_encodes_whole_clip(
    client, mocker, tmp_path
):
    """No trim_end and no duration used to become a 0.1s clip; now no -t at all."""
    (tmp_path / "clip.mp4").write_text("video", encoding="utf-8")
    calls = []
    _fake_ffmpeg(mocker, calls)

    resp = _export(client, [{"filename": "clip.mp4"}])

    assert resp.status_code == 200
    trim_cmd = calls[0]
    assert "-t" not in trim_cmd
    assert "yuv420p" in trim_cmd  # normalised encode settings


@pytest.mark.security
@pytest.mark.parametrize("path", ["/api/clips", "/", "/api/exports"])
def test_non_loopback_host_is_rejected(client, path):
    resp = client.get(path, headers={"Host": "evil.test"})
    assert resp.status_code == 403


@pytest.mark.security
def test_forged_host_and_origin_post_is_rejected(client, mocker):
    """DNS rebinding: attacker page sends Host and Origin that match each other."""
    run = mocker.patch("manimgen.editor.server.subprocess.run")
    resp = client.post(
        "/api/export",
        data=json.dumps({"clips": [{"filename": "a.mp4"}]}),
        headers={
            "Host": "evil.test:5001",
            "Origin": "http://evil.test:5001",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403
    run.assert_not_called()


@pytest.mark.security
@pytest.mark.parametrize(
    "host", ["localhost", "localhost:5001", "127.0.0.1:5001", "[::1]:5001"]
)
def test_loopback_hosts_are_allowed(client, host):
    assert client.get("/api/exports", headers={"Host": host}).status_code == 200


@pytest.mark.security
@pytest.mark.parametrize(
    "host", ["localhost.evil.test", "127.0.0.1.evil.test", "evil.test:5001"]
)
def test_lookalike_hosts_are_rejected(client, host):
    assert client.get("/api/exports", headers={"Host": host}).status_code == 403


@pytest.mark.unit
def test_clip_durations_are_probed_once_per_unchanged_file(client, mocker, tmp_path):
    server._DURATION_CACHE.clear()
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    probe = mocker.patch("manimgen.editor.server._probe_duration", return_value=2.0)

    first = client.get("/api/clips").get_json()
    client.get("/api/clips")

    assert [c["duration"] for c in first] == [2.0, 2.0]
    assert probe.call_count == 2

    (tmp_path / "a.mp4").write_text("changed content", encoding="utf-8")
    client.get("/api/clips")
    assert probe.call_count == 3


@pytest.mark.unit
def test_failed_probe_is_not_cached(client, mocker, tmp_path):
    server._DURATION_CACHE.clear()
    (tmp_path / "a.mp4").write_text("x", encoding="utf-8")
    probe = mocker.patch("manimgen.editor.server._probe_duration", return_value=None)

    client.get("/api/clips")
    client.get("/api/clips")

    assert probe.call_count == 2


@pytest.mark.unit
def test_server_binds_loopback_only(mocker, tmp_path):
    mocker.patch("manimgen.editor.server.webbrowser.open")
    run = mocker.patch.object(server.app, "run")
    mocker.patch("sys.argv", ["manimgen-edit", "--videos", str(tmp_path)])
    server.main()
    assert run.call_args.kwargs["host"] == "127.0.0.1"


@pytest.mark.unit
def test_dead_output_dir_state_is_gone():
    assert not hasattr(server, "OUTPUT_DIR")
