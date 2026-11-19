"""The step timer, its table, timings.json and the hooks that feed it."""

from __future__ import annotations

import json
import threading
import time

import pytest

from manimgen import cli, paths, timing
from manimgen.timing import StepTimer


def _timer_with(*calls) -> StepTimer:
    """A timer holding (step, start, end) calls, times relative to its origin."""
    t = StepTimer()
    base = t._origin
    for step, start, end, *rest in calls:
        t.record(step, base + start, base + end, *(rest or [""]))
    return t


def test_section_records_a_call():
    t = StepTimer()
    with t.section("narration", "section_01"):
        time.sleep(0.02)
    (entry,) = t.entries()
    assert entry["step"] == "narration"
    assert entry["label"] == "section_01"
    assert entry["seconds"] >= 0.02


def test_section_records_even_when_the_body_raises():
    t = StepTimer()
    with pytest.raises(RuntimeError):
        with t.section("assembly"):
            raise RuntimeError("boom")
    assert [e["step"] for e in t.entries()] == ["assembly"]


def test_overlapping_calls_count_once_in_wall_and_twice_in_busy():
    t = _timer_with(("director", 0, 10), ("director", 5, 15), ("render", 20, 30))
    rows = {r["step"]: r for r in t.summary(total=40)}
    assert rows["director"]["seconds"] == 15.0
    assert rows["director"]["busy_seconds"] == 20.0
    assert rows["director"]["calls"] == 2
    assert rows["director"]["share"] == pytest.approx(15 / 40, abs=0.001)
    assert rows["render"]["share"] == pytest.approx(0.25)


def test_unmeasured_is_what_no_step_covers():
    t = _timer_with(("planning", 0, 10), ("render", 5, 20))
    assert t.unmeasured(total=30) == pytest.approx(10.0)


def test_threads_record_without_losing_calls():
    t = StepTimer()

    def work(n):
        for i in range(50):
            with t.section("render", f"{n}-{i}"):
                pass

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(t.entries()) == 400
    assert t.summary()[0]["calls"] == 400


def test_parallel_sleeping_threads_have_wall_below_busy():
    t = StepTimer()

    def work():
        with t.section("render"):
            time.sleep(0.15)

    threads = [threading.Thread(target=work) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    (row,) = t.summary()
    assert row["busy_seconds"] >= 0.4
    assert row["seconds"] < row["busy_seconds"] * 0.6


def test_context_labels_are_per_thread():
    seen = {}

    def work(name):
        with timing.context(name):
            time.sleep(0.05)
            seen[name] = timing.current_label()

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert seen == {"a": "a", "b": "b"}
    assert timing.current_label() == "render"


def test_roles_map_to_steps():
    assert timing.step_for_role("researcher") == "planning"
    assert timing.step_for_role("critic") == "planning"
    assert timing.step_for_role("director") == "director"
    for role in ("error_fix", "visual_fix", "layout_check"):
        assert timing.step_for_role(role) == "fixes"
    assert timing.step_for_role(None) == "other"
    assert timing.step_for_role("something_new") == "other"


def test_table_has_one_line_per_step_with_seconds_and_share():
    t = _timer_with(
        ("planning", 0, 20),
        ("director", 20, 80),
        ("render", 80, 380, "first render"),
    )
    t.record(
        "render", t._origin + 380, t._origin + 390, "x", resolution="540x960", fps=30
    )
    lines = t.table(total=400)
    assert "Time per step" in lines[0] and "6m40s" in lines[0]
    by_step = {line.split()[0]: line for line in lines[1:]}
    assert "60.0s" in by_step["director"] and "15%" in by_step["director"]
    assert "2 calls" in by_step["render"] and "1 at 540x960@30" in by_step["render"]
    assert "unmeasured" in by_step


def test_to_dict_has_steps_calls_and_extra():
    t = _timer_with(("render", 1, 4, "first render"))
    t.record(
        "render",
        t._origin + 5,
        t._origin + 6,
        "draft render A",
        resolution="540x960",
        fps=30,
    )
    data = t.to_dict(total=10, profile="draft")
    assert data["total_seconds"] == 10
    assert data["profile"] == "draft"
    assert data["steps"][0]["step"] == "render"
    assert data["render_profiles"] == {"540x960@30": 1}
    assert [c["label"] for c in data["calls"]] == ["first render", "draft render A"]


def test_write_creates_json_and_can_skip_creating_the_folder(tmp_path):
    t = _timer_with(("assembly", 0, 2))
    missing = tmp_path / "nope"
    assert t.write(str(missing), total=5, create=False) is None
    assert not missing.exists()
    path = t.write(str(tmp_path / "logs"), total=5, profile="final")
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["profile"] == "final"
    assert data["steps"][0]["step"] == "assembly"


def test_timed_render_records_size_and_fps(monkeypatch):
    class Result:
        ok = True

    @timing.timed_render
    def fake(scene_path, class_name, timeout=None, draft=None):
        return Result()

    timing.TIMER.reset()
    paths.set_video_format("short")
    with timing.context("draft render B"):
        fake("s.py", "Scene", draft=True)
    fake("s.py", "Scene")
    first, second = timing.TIMER.entries()
    assert (first["label"], first["resolution"], first["fps"]) == (
        "draft render B",
        "540x960",
        30,
    )
    assert (second["label"], second["resolution"], second["fps"]) == (
        "render",
        "1080x1920",
        paths.MANIMGL_DEFAULT_FPS,
    )
    assert first["ok"] is True


def test_timed_render_records_a_raising_render():
    @timing.timed_render
    def boom(scene_path, class_name):
        raise ValueError("x")

    timing.TIMER.reset()
    with pytest.raises(ValueError):
        boom("s.py", "S")
    assert timing.TIMER.entries()[0]["ok"] is False


def test_llm_chat_records_its_call_by_role(monkeypatch):
    from manimgen import llm

    monkeypatch.setattr(llm, "_resolve_provider", lambda: "ollama")
    monkeypatch.setattr(llm, "_ollama", lambda *a, **k: "hi")
    monkeypatch.setattr(llm, "_record_call", lambda **k: None)
    timing.TIMER.reset()
    llm.chat("sys", "user", role="director")
    entries = timing.TIMER.entries()
    assert [(e["step"], e["label"]) for e in entries] == [("director", "director")]


def test_finish_run_prints_table_and_writes_timings_json(tmp_path, capsys, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setitem(paths._PATHS, "logs", str(logs))
    timing.TIMER.reset()
    with timing.TIMER.section("assembly"):
        pass
    paths.set_draft(True)
    paths.set_video_format("short")
    out = tmp_path / "v.mp4"
    out.write_bytes(b"x")
    cli._finish_run(
        "T",
        "h",
        [
            {
                "index": 1,
                "id": "s1",
                "title": "t",
                "status": "ok",
                "reason": "",
                "clips": 1,
                "seconds": 1,
            }
        ],
        str(out),
        0,
        "ok",
        time.time(),
    )
    text = capsys.readouterr().out
    # The existing summary lines are untouched, the table comes after them.
    assert 'Run summary for "T": 1 sections (1 ok)' in text
    assert text.index("Run summary") < text.index("Time per step")
    data = json.loads((logs / "timings.json").read_text(encoding="utf-8"))
    assert data["profile"] == "draft"
    assert data["resolution"] == "540x960"
    assert data["fps"] == paths.render_fps(True)
    assert data["steps"][0]["step"] == "assembly"
