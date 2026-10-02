"""Console-entry-point tests for cli.main(): logging (#63) and --resume (#64).

main() is the function the installed `manimgen` console script calls
(setup.py entry_points), so these tests resolve it from that entry-point string
and call it directly with sys.argv set, not via `python -m`. Only leaf seams
are mocked: the planners (LLM), _run_section (render/TTS/subprocess) and
assemble_video (ffmpeg). Zero LLM, TTS or subprocess calls.
"""

import importlib
import json
import logging
import os
import re

import pytest

from manimgen import cli, paths

_SETUP = os.path.join(os.path.dirname(__file__), "..", "setup.py")


def _console_main():
    """Resolve the callable the `manimgen` console script invokes."""
    with open(_SETUP, encoding="utf-8") as f:
        target = re.search(r'"manimgen=([\w.]+:\w+)"', f.read()).group(1)
    mod, func = target.split(":")
    return getattr(importlib.import_module(mod), func)


def _plan(title="Bubble Sort", topic_hash=None):
    plan = {"title": title, "sections": [{"id": "section_01", "narration": "hi"}]}
    if topic_hash is not None:
        plan["_topic_hash"] = topic_hash
    return plan


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated plan cache + logs dir, stubbed leaf seams, clean root logger."""
    plan_path = str(tmp_path / "cache" / "plan.json")
    logs = str(tmp_path / "logs")
    monkeypatch.setattr(cli, "_PLAN_CACHE", plan_path)
    monkeypatch.setitem(paths._PATHS, "logs", logs)
    monkeypatch.setattr(cli, "_load_config", lambda: {})
    calls = {"plan_lesson": 0, "plan_pdf": 0}

    def fake_plan_lesson(topic):
        calls["plan_lesson"] += 1
        return _plan()

    def fake_plan_pdf(pdf):
        calls["plan_pdf"] += 1
        return _plan("From Pdf")

    monkeypatch.setattr(cli, "plan_lesson", fake_plan_lesson)
    monkeypatch.setattr(cli, "plan_lesson_from_pdf", fake_plan_pdf)
    monkeypatch.setattr(cli, "_run_section", lambda *a, **k: ["clip.mp4"])
    out = str(tmp_path / "final.mp4")
    with open(out, "wb") as f:
        f.write(b"x")
    monkeypatch.setattr(cli, "assemble_video", lambda clips, title: out)

    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    root.setLevel(logging.WARNING)
    yield {"plan_path": plan_path, "logs": logs, "calls": calls, "out": out}
    for h in list(root.handlers):
        if h not in saved[0]:
            root.removeHandler(h)
            h.close()
    root.setLevel(saved[1])


def _run(monkeypatch, *argv):
    # pytest's logging plugin adds its own capture handlers to the root logger
    # during the call phase; remove them so main() sees what a real console
    # launch sees (an unconfigured root). The fixture teardown restores state.
    root = logging.getLogger()
    for h in list(root.handlers):
        if type(h).__module__.startswith("_pytest"):
            root.removeHandler(h)
    monkeypatch.setattr("sys.argv", ["manimgen", *argv])
    _console_main()()


def _write_plan(env, plan):
    os.makedirs(os.path.dirname(env["plan_path"]), exist_ok=True)
    with open(env["plan_path"], "w", encoding="utf-8") as f:
        json.dump(plan, f)


# --- #63: logging and the final path ---------------------------------------


class TestLogging:
    def test_progress_and_output_path_are_shown(self, env, monkeypatch, capfd):
        _run(monkeypatch, "bubble sort")
        cap = capfd.readouterr()
        text = cap.out + cap.err
        assert "[manimgen] Planned 1 sections" in text
        assert "Done: " + env["out"] in cap.out

    def test_run_log_file_written_with_debug(self, env, monkeypatch):
        _run(monkeypatch, "bubble sort")
        files = [f for f in os.listdir(env["logs"]) if f.startswith("run_")]
        assert len(files) == 1
        for h in logging.getLogger().handlers:
            h.flush()
        with open(os.path.join(env["logs"], files[0]), encoding="utf-8") as f:
            assert "Planned 1 sections" in f.read()

    def test_not_double_configured(self, env, monkeypatch):
        _run(monkeypatch, "bubble sort")
        n = len(logging.getLogger().handlers)
        assert n >= 1
        _run(monkeypatch, "bubble sort")
        assert len(logging.getLogger().handlers) == n

    def test_existing_host_logging_is_respected(self, env, monkeypatch):
        host = logging.NullHandler()
        logging.getLogger().addHandler(host)
        _run(monkeypatch, "bubble sort")
        assert logging.getLogger().handlers == [host]
        assert not os.path.exists(env["logs"])

    def test_no_video_produced_exits_nonzero(self, env, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_run_section", lambda *a, **k: [])
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "bubble sort")
        assert e.value.code == 1
        assert "No video was produced" in capsys.readouterr().err

    def test_output_missing_on_disk_exits_nonzero(self, env, monkeypatch, capsys):
        os.remove(env["out"])
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "bubble sort")
        assert e.value.code == 1
        assert "No video was produced" in capsys.readouterr().err


# --- #64: --resume ----------------------------------------------------------


class TestResume:
    def test_resume_alone_with_cache_works(self, env, monkeypatch, capsys):
        _write_plan(env, _plan(topic_hash="abcd1234"))
        _run(monkeypatch, "--resume")
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}
        assert "Done: " in capsys.readouterr().out

    def test_resume_alone_without_cache_refuses(self, env, monkeypatch, capsys):
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "--resume")
        assert e.value.code == 1
        assert env["plan_path"] in capsys.readouterr().err
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_topic_with_resume_matching_hash_works(self, env, monkeypatch):
        h = cli._topic_hash("bubble sort")
        _write_plan(env, _plan(topic_hash=h))
        seen = {}

        def fake_run_section(s, i, t, th, **k):
            seen["hash"] = th
            return ["c.mp4"]

        monkeypatch.setattr(cli, "_run_section", fake_run_section)
        _run(monkeypatch, "bubble sort", "--resume")
        assert seen["hash"] == h
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_topic_with_resume_mismatch_refuses(self, env, monkeypatch, capsys):
        _write_plan(env, _plan("Old Title", topic_hash=cli._topic_hash("other")))
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "bubble sort", "--resume")
        assert e.value.code == 1
        err = capsys.readouterr().err
        assert "Old Title" in err and "bubble sort" in err
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_topic_with_resume_missing_plan_refuses(self, env, monkeypatch):
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "bubble sort", "--resume")
        assert e.value.code == 1
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_pdf_with_resume_mismatch_refuses(self, env, monkeypatch):
        _write_plan(env, _plan(topic_hash=cli._topic_hash("bubble sort")))
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "--pdf", "notes.pdf", "--resume")
        assert e.value.code == 1
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_pdf_with_resume_matching_hash_works(self, env, monkeypatch):
        _write_plan(env, _plan(topic_hash=cli._topic_hash(os.path.abspath("n.pdf"))))
        _run(monkeypatch, "--pdf", "n.pdf", "--resume")
        assert env["calls"] == {"plan_lesson": 0, "plan_pdf": 0}

    def test_topic_with_resume_plan_without_hash_refuses(self, env, monkeypatch):
        _write_plan(env, _plan())
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "bubble sort", "--resume")
        assert e.value.code == 1

    def test_corrupt_plan_refuses(self, env, monkeypatch, capsys):
        os.makedirs(os.path.dirname(env["plan_path"]))
        with open(env["plan_path"], "w", encoding="utf-8") as f:
            f.write("{not json")
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "--resume")
        assert e.value.code == 1
        assert "corrupt" in capsys.readouterr().err.lower()

    def test_plan_without_sections_refuses(self, env, monkeypatch):
        _write_plan(env, {"title": "x"})
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "--resume")
        assert e.value.code == 1

    def test_no_input_at_all_is_a_usage_error(self, env, monkeypatch):
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch)
        assert e.value.code == 2

    def test_topic_and_pdf_still_exclusive(self, env, monkeypatch):
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, "t", "--pdf", "a.pdf")
        assert e.value.code == 2

    def test_fresh_plan_saved_atomically(self, env, monkeypatch):
        _run(monkeypatch, "bubble sort")
        with open(env["plan_path"], encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["_topic_hash"] == cli._topic_hash("bubble sort")
        assert os.listdir(os.path.dirname(env["plan_path"])) == ["plan.json"]
