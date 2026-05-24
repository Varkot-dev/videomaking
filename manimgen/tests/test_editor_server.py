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
        return_value=mocker.MagicMock(stdout=json.dumps({"format": {"duration": "N/A"}})),
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
    resp = client.post(
        "/api/export", data="", headers={"Origin": "http://localhost"}
    )
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
    src.write_text("video")

    captured = {}

    def fake_run(cmd, **kwargs):
        # Record the output path of the final concat (last arg).
        if cmd and cmd[0] == "ffmpeg":
            out = cmd[-1]
            if out.endswith(".mp4"):
                # touch outputs so finally-cleanup and concat see files
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text("out")
                if "concat" in cmd:
                    captured["concat_out"] = out
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
    assert Path(captured["concat_out"]).name == f"{expected}.mp4"


# ── XSS: the template no longer interpolates clip.id into markup ─────────────

_EDITOR_HTML = (
    Path(server.__file__).parent / "templates" / "editor.html"
).read_text()


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
