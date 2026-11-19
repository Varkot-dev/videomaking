"""Burned-in Shorts captions: chunking, ASS output, placement, flag wiring and
one real ffmpeg burn. No LLM, no TTS."""

import shutil
import subprocess
import sys

import pytest

from manimgen import cli, config, paths
from manimgen.renderer import captions as cap
from manimgen.renderer.captions import Caption, CaptionStyle
from manimgen.renderer.tts import WordTimestamp as W


def words(*spec):
    """spec: (word, start, end)"""
    return [W(*s) for s in spec]


def run_chunks(ws, narration=None, **kw):
    return cap.chunk_words(ws, narration, **kw)


# --- chunking ---------------------------------------------------------------


def test_max_four_words_per_chunk():
    ws = [W(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(9)]
    chunks = run_chunks(ws)
    assert [len(c.text.split()) for c in chunks] == [4, 4, 1]


def test_breaks_at_punctuation_from_narration():
    ws = words(("so", 0, 0.2), ("what", 0.25, 0.5), ("now", 0.55, 0.8), ("we", 0.85, 1))
    chunks = run_chunks(ws, "So what? Now, we")
    assert [c.text for c in chunks] == ["so what?", "now,", "we"]


def test_breaks_at_punctuation_in_word_text():
    ws = words(("Hi.", 0, 0.2), ("there", 0.25, 0.5))
    assert [c.text for c in run_chunks(ws)] == ["Hi.", "there"]


def test_breaks_at_pause():
    ws = words(("a", 0, 0.2), ("b", 0.25, 0.4), ("c", 1.2, 1.4))
    chunks = run_chunks(ws)
    assert [c.text for c in chunks] == ["a b", "c"]


def test_chunk_spans_first_start_to_last_end():
    ws = words(("a", 1.0, 1.2), ("b", 1.25, 1.6))
    (c,) = run_chunks(ws)
    assert (c.start, c.end) == (1.0, 1.6)


def test_small_gap_is_bridged_and_big_gap_is_not():
    ws = words(("a", 0, 0.5), ("b.", 0.55, 1.0), ("c", 1.2, 1.5), ("d", 3.0, 3.4))
    a, c, d = run_chunks(ws)[0], run_chunks(ws)[1], run_chunks(ws)[2]
    assert a.end == pytest.approx(c.start)  # 0.2 s gap bridged
    assert c.end == pytest.approx(1.5)  # 1.5 s gap left alone
    assert d.start == 3.0


def test_long_words_split_by_character_budget():
    ws = [W("extraordinarily", i * 0.5, i * 0.5 + 0.4) for i in range(3)]
    assert len(run_chunks(ws)) == 3


def test_offset_moves_everything():
    ws = words(("a", 0, 0.5))
    (c,) = run_chunks(ws, offset=10.0)
    assert (c.start, c.end) == (10.0, 10.5)


def test_no_words_no_captions():
    assert run_chunks([]) == []


def test_section_offsets_account_for_crossfade():
    assert cap.section_offsets([20.0]) == [0.0]
    offs = cap.section_offsets([10.0, 10.0])
    assert offs[0] == 0.0
    assert 9.0 < offs[1] < 10.0  # the next section starts inside the fade


def test_captions_for_sections_offsets_second_section():
    a = words(("one", 0, 0.5))
    b = words(("two", 0, 0.5))
    out = cap.captions_for_sections([(a, None, 10.0), (b, None, 10.0)])
    assert out[0].start == 0.0
    assert 9.0 < out[1].start < 10.0


# --- ASS --------------------------------------------------------------------


def test_ass_contents():
    st = CaptionStyle()
    ass = cap.build_ass([Caption("Hello {x}", 1.0, 2.5)], st)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.50,Caption,,0,0,0,,Hello (x)" in ass
    style = [ln for ln in ass.splitlines() if ln.startswith("Style:")][0]
    fields = style.split(":", 1)[1].split(",")
    assert fields[1] == "Arial"
    assert fields[7] == "-1"  # bold
    assert fields[3] == "&H00FFFFFF"  # white text
    assert fields[18] == "2"  # bottom centre


def test_ass_time_format():
    assert cap._ass_time(3725.5) == "1:02:05.50"


# --- placement --------------------------------------------------------------


def test_default_position_is_above_the_shorts_ui_and_inside_safe_area():
    st = CaptionStyle.from_config()
    assert st.margin_bottom_px >= 480
    assert st.margin_left_px == 48
    assert st.margin_right_px == 192
    assert config.section("shorts")["captions"]["enabled"] is False


def test_margins_reach_the_ass_style():
    st = CaptionStyle(margin_bottom_px=600, margin_left_px=60, margin_right_px=200)
    ass = cap.build_ass([Caption("x", 0, 1)], st)
    style = [ln for ln in ass.splitlines() if ln.startswith("Style:")][0]
    f = style.split(":", 1)[1].split(",")
    assert (f[19], f[20], f[21]) == ("60", "200", "600")


def test_config_override_is_read_and_validated(tmp_path, monkeypatch):
    src = config.config_path().read_text(encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        src + "\n", encoding="utf-8"
    )  # same file; then replace the captions block
    text = src.replace("margin_bottom_px: 488", "margin_bottom_px: 700")
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("MANIMGEN_CONFIG", str(cfg))
    loaded = config._validate(cfg, config._read(cfg))
    assert loaded["shorts"]["captions"]["margin_bottom_px"] == 700
    bad = tmp_path / "bad.yaml"
    bad.write_text(src.replace("margin_bottom_px: 488", "margin_bottom_px: tall"))
    with pytest.raises(config.ConfigError):
        config._validate(bad, config._read(bad))


def test_partial_captions_block_keeps_other_defaults(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("shorts:\n  captions:\n    enabled: true\n", encoding="utf-8")
    loaded = config._validate(cfg, config._read(cfg))
    caps = loaded["shorts"]["captions"]
    assert caps["enabled"] is True and caps["margin_bottom_px"] == 488


def test_drawtext_fallback_filter_uses_position():
    st = CaptionStyle(margin_bottom_px=500)
    vf = cap.build_drawtext_filter([Caption("it's 100%", 1, 2)], st)
    assert "y=h-500-text_h" in vf
    assert "between(t,1.000,2.000)" in vf
    assert "'" not in vf.split("text='")[1].split("':")[0]


# --- Windows path escaping --------------------------------------------------


def test_windows_path_escaping():
    assert (
        cap.escape_filter_path("C:\\work\\out\\_cap_x\\captions.ass")
        == "C\\:/work/out/_cap_x/captions.ass"
    )


def test_posix_path_unchanged_and_odd_chars_escaped():
    assert cap.escape_filter_path("/tmp/a/b.ass") == "/tmp/a/b.ass"
    assert (
        cap.escape_filter_path("/tmp/it's [x],y.ass") == "/tmp/it\\'s \\[x\\]\\,y.ass"
    )


# --- skipping and fallback --------------------------------------------------


def test_add_captions_skips_without_timestamps(tmp_path, caplog):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    with caplog.at_level("INFO"):
        assert cap.add_captions(str(v), [([], "", 5.0)]) is False
    assert "Skipped" in caplog.text
    assert v.read_bytes() == b"x"


def test_burn_uses_timeouts_and_drawtext_fallback(tmp_path, monkeypatch):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[0] == "ffmpeg" and "-filters" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, " T.C drawtext          V->V       text\n", ""
            )
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "5.0\n", "")
        (tmp_path / "unused").write_text("")
        open(cmd[-1], "wb").write(b"burned")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    cap.available_filters.cache_clear()
    monkeypatch.setattr(cap.subprocess, "run", fake_run)
    try:
        cap.burn_captions(str(v), [Caption("hi", 0, 1)], style=CaptionStyle())
    finally:
        cap.available_filters.cache_clear()
    assert v.read_bytes() == b"burned"
    assert all(kw.get("timeout") for _, kw in calls)
    encode = [c for c, _ in calls if "-vf" in c][0]
    assert encode[encode.index("-vf") + 1].startswith("drawtext=")


# --- CLI wiring (mocked) ----------------------------------------------------


def _drive_main(monkeypatch, tmp_path, argv, cfg_enabled=False):
    """Run cli.main with the heavy parts replaced; return (add_captions calls)."""
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    plan = {
        "title": "T",
        "_format": "short",
        "sections": [{"id": "section_01", "title": "S", "narration": "Hi there."}],
    }
    audio = {
        "section_01": {
            "timestamps": words(("Hi", 0, 0.3), ("there", 0.35, 0.7)),
            "audio_duration": 1.0,
            "cue_durations": [1.0],
        }
    }
    got = []
    cfg = config.load()
    cfg = {**cfg, "shorts": {**cfg["shorts"]}}
    cfg["shorts"]["captions"] = {**cfg["shorts"]["captions"], "enabled": cfg_enabled}
    monkeypatch.setattr(sys, "argv", ["manimgen", *argv])
    monkeypatch.setattr(cli, "_load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "plan_short", lambda *a, **k: plan)
    monkeypatch.setattr(cli, "plan_lesson", lambda *a, **k: plan)
    monkeypatch.setattr(cli, "_tts_enabled", lambda c: False)
    monkeypatch.setattr(cli, "assemble_video", lambda clips, title: str(video))
    monkeypatch.setattr(cli, "_add_captions", lambda *a: got.append(a))

    class Out:
        clips = [str(video)]
        status = cli.SectionStatus.OK
        reason = ""

    monkeypatch.setattr(cli, "_run_section", lambda *a, **k: Out())
    monkeypatch.setattr(cli, "_section_record", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(cli, "_finish_run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_build_overview", lambda *a, **k: {"pacing_notes": ""})
    monkeypatch.setattr(cli, "reset_run_budget", lambda: None, raising=False)
    del audio
    try:
        cli.main()
    except SystemExit:
        pass
    return got


@pytest.mark.parametrize(
    "argv,enabled,expect",
    [
        (["topic", "--short", "--captions"], False, True),
        (["topic", "--short"], False, False),
        (["topic", "--short"], True, True),
        (["topic", "--captions"], True, False),  # not a short: ignored
    ],
)
def test_flag_wiring(monkeypatch, tmp_path, argv, enabled, expect):
    paths.set_video_format("landscape")
    try:
        got = _drive_main(monkeypatch, tmp_path, argv, enabled)
    finally:
        paths.set_video_format("landscape")
    assert bool(got) is expect


def test_cli_add_captions_passes_word_timestamps(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cap, "add_captions", lambda out, parts: seen.update(out=out, parts=parts)
    )
    plan = {"sections": [{"id": "section_01", "title": "S", "narration": "Hi"}]}
    ts = words(("Hi", 0, 0.3))
    cli._add_captions(
        "v.mp4", plan, {"section_01": {"timestamps": ts, "audio_duration": 1.0}}
    )
    assert seen["out"] == "v.mp4"
    assert seen["parts"] == [(ts, "Hi", 1.0)]
    # Narration off: no entry, so add_captions will skip.
    cli._add_captions("v.mp4", plan, {})
    assert seen["parts"] == []


# --- real ffmpeg burn -------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def _frame(path, t, out):
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t), "-i", str(path), "-frames:v", "1", str(out)],
        check=True,
        capture_output=True,
        timeout=30,
    )


@needs_ffmpeg
def test_real_burn_changes_caption_region_and_keeps_duration(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    np = pytest.importorskip("numpy")
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "testsrc=size=1080x1920:rate=15:duration=3",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )  # fmt: skip
    plain = tmp_path / "plain.mp4"
    shutil.copy(src, plain)
    ws = words(("Captions", 0.2, 0.8), ("work", 0.85, 1.4), ("here", 1.45, 2.5))
    chunks = cap.chunk_words(ws)
    out = tmp_path / "out.mp4"
    cap.burn_captions(str(src), chunks, str(out), CaptionStyle())
    assert out.exists()

    def dur(p):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(p)],
            capture_output=True, text=True, check=True, timeout=15,
        )  # fmt: skip
        return float(r.stdout)

    assert dur(out) == pytest.approx(dur(src), abs=0.15)

    _frame(src, 1.0, tmp_path / "a.png")
    _frame(out, 1.0, tmp_path / "b.png")
    a = np.asarray(PIL.open(tmp_path / "a.png").convert("L"), dtype=int)
    b = np.asarray(PIL.open(tmp_path / "b.png").convert("L"), dtype=int)
    # Caption band: bottom edge 488 px above the frame bottom, ~110 px tall.
    top, bottom = 1920 - 488 - 110, 1920 - 488 + 10
    assert np.abs(a[top:bottom] - b[top:bottom]).mean() > 1.0
    # Nothing below the safe line (the Shorts UI band) changed much.
    assert np.abs(a[1920 - 470 :] - b[1920 - 470 :]).mean() < 3.0
    # The same moment after the last caption is gone is untouched.
    _frame(out, 2.9, tmp_path / "c.png")
    _frame(src, 2.9, tmp_path / "d.png")
    c = np.asarray(PIL.open(tmp_path / "c.png").convert("L"), dtype=int)
    d = np.asarray(PIL.open(tmp_path / "d.png").convert("L"), dtype=int)
    assert np.abs(c[top:bottom] - d[top:bottom]).mean() < 3.0
