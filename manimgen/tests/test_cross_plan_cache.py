"""Regression tests for issue #66 (R01): per-section artifacts must never be
reused across different plans.

Every per-section artifact (audio slices, cut clips, muxed clips, renders)
lives in a shared folder named only by section id, and planners emit generic
ids (section_01, ...). Before the fix, running topic A and then topic B from
the same folder shipped A's video and narration for B: the muxed shortcut,
the audio slicer and the render cache all trusted any existing file.

These tests drive cli.main() end to end through the real section pipeline.
Only the leaf seams are faked: the planner and Director LLM calls, edge-tts,
the manimgl render subprocess and the ffmpeg calls. Each fake writes a text
"media" file that records where its content came from, so the final clip list
handed to the assembler shows exactly which plan's narration and video ship.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

import manimgen.renderer.audio_slicer as audio_slicer
import manimgen.renderer.muxer as muxer
import manimgen.renderer.tts as tts
import manimgen.validator.render_validator as render_validator
from manimgen import cli

_WORD_SECONDS = 0.5


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _plan(title: str, narrations: list[str], cue_indices: list[list[int]]) -> dict:
    sections = []
    for i, (narration, cues) in enumerate(zip(narrations, cue_indices), start=1):
        sections.append(
            {
                "id": f"section_{i:02d}",
                "title": f"{title} part {i}",
                "narration": narration,
                "cue_word_indices": cues,
                "cues": [
                    {"index": n, "visual": f"{title} visual {n}"}
                    for n in range(len(cues))
                ],
            }
        )
    return {"title": title, "sections": sections}


class _Fakes:
    """Leaf-seam fakes plus counters the tests assert on."""

    def __init__(self, tmp_path, monkeypatch):
        self.dirs = {
            "audio": str(tmp_path / "audio"),
            "muxed": str(tmp_path / "muxed"),
            "videos": str(tmp_path / "videos"),
            "scenes": str(tmp_path / "scenes"),
        }
        for d in self.dirs.values():
            os.makedirs(d, exist_ok=True)
        self.plan_cache = str(tmp_path / "plan.json")
        self.plan: dict = {}
        self.assembled: list[str] = []
        self.codegen_calls = 0
        self.mux_calls = 0
        self.slice_calls = 0

        mp = monkeypatch
        mp.setattr(cli.paths, "audio_dir", lambda: self.dirs["audio"])
        mp.setattr(cli.paths, "muxed_dir", lambda: self.dirs["muxed"])
        mp.setattr(cli.paths, "videos_dir", lambda: self.dirs["videos"])
        mp.setattr(cli.paths, "scenes_dir", lambda: self.dirs["scenes"])
        mp.setattr(cli, "_PLAN_CACHE", self.plan_cache)
        mp.setattr(cli, "_load_config", lambda: {"tts": {"enabled": True}})

        # LLM: planner and Director.
        mp.setattr(cli, "plan_lesson", lambda topic: json.loads(json.dumps(self.plan)))
        mp.setattr(
            cli, "plan_lesson_from_pdf", lambda pdf: json.loads(json.dumps(self.plan))
        )
        mp.setattr(cli, "generate_scenes", self._generate_scenes)

        # edge-tts network and ffprobe.
        mp.setattr(tts, "generate_narration", self._generate_narration)
        mp.setattr(tts, "get_audio_duration", self._audio_duration)
        mp.setattr(tts, "check_audio_not_silent", lambda p: {"ok": True})

        # ffmpeg (audio slicing, cutting, muxing).
        mp.setattr(audio_slicer, "_check_ffmpeg", lambda: None)
        mp.setattr(audio_slicer, "_ffmpeg_slice", self._slice)
        mp.setattr(audio_slicer, "_ffmpeg_copy", self._copy)
        mp.setattr(muxer, "_cut_one", self._cut)
        mp.setattr(muxer, "mux_audio_video", self._mux)

        # manimgl render subprocess and the frame checks that shell out.
        mp.setattr(cli, "run_scene", self._run_scene)
        mp.setattr(cli, "_find_rendered_video", self._find_rendered)
        mp.setattr(
            render_validator,
            "validate_render",
            lambda *a, **k: SimpleNamespace(ok=True, issues=[], severity="none"),
        )
        mp.setattr(cli, "assemble_video", self._assemble)

    # -- LLM / render ------------------------------------------------------
    def _generate_scenes(self, section, cue_durations=None, overview=None):
        self.codegen_calls += 1
        from manimgen.utils import section_class_name

        class_name = section_class_name(section)
        lines = [f"# {section['title']}: {section['narration']}"]
        for i, d in enumerate(cue_durations or []):
            lines.append(f"# CUE {i} — {d:.2f}s")
            lines.append(f"self.wait({d:.2f})")
        code = "\n".join(lines) + "\n"
        scene_path = os.path.join(self.dirs["scenes"], f"{section['id']}.py")
        _write(scene_path, code)
        return code, class_name, scene_path

    def _run_scene(self, scene_path, class_name):
        out = os.path.join(self.dirs["videos"], f"{class_name}.mp4")
        _write(out, "render<" + _read(scene_path).splitlines()[0] + ">")
        return True, out

    def _find_rendered(self, class_name):
        p = os.path.join(self.dirs["videos"], f"{class_name}.mp4")
        return p if os.path.exists(p) else None

    # -- TTS ----------------------------------------------------------------
    def _generate_narration(self, text, output_path, voice=None):
        _write(output_path, f"voice<{text}>")
        words = text.split()
        stamps = [
            tts.WordTimestamp(
                word=w, start=i * _WORD_SECONDS, end=(i + 1) * _WORD_SECONDS
            )
            for i, w in enumerate(words)
        ]
        return output_path, stamps

    def _audio_duration(self, audio_path):
        text = _read(audio_path)[len("voice<") : -1]
        return len(text.split()) * _WORD_SECONDS

    # -- ffmpeg -------------------------------------------------------------
    def _slice(self, input_path, output_path, start, end):
        self.slice_calls += 1
        _write(output_path, f"slice[{_read(input_path)}@{start:.2f}]")

    def _copy(self, input_path, output_path):
        self.slice_calls += 1
        _write(output_path, f"slice[{_read(input_path)}@0.00]")

    def _cut(self, video_path, start, dur, out_path, i):
        _write(out_path, f"cut[{_read(video_path)}#{i}]")
        return out_path

    def _mux(self, video_path, audio_path, output_path):
        self.mux_calls += 1
        _write(output_path, f"{_read(video_path)}+{_read(audio_path)}")
        return output_path

    def _assemble(self, clips, title):
        self.assembled = list(clips)
        # main() now fails a run whose final video is missing on disk (#63).
        out = os.path.join(self.dirs["videos"], "final.mp4")
        _write(out, "final")
        return out

    # -- driver ---------------------------------------------------------------
    def run(self, monkeypatch, plan: dict | None, topic: str, resume=False, pdf=None):
        if plan is not None:
            self.plan = plan
        if pdf:
            argv = ["manimgen", "--pdf", pdf]
        elif resume:
            argv = ["manimgen", "--resume", topic]
        else:
            argv = ["manimgen", topic]
        monkeypatch.setattr(sys, "argv", argv)
        self.assembled = []
        cli.main()
        return [_read(p) for p in self.assembled]


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    return _Fakes(tmp_path, monkeypatch)


GD = _plan(
    "gradient",
    ["gradient descent walks downhill step by step", "the learning rate matters"],
    [[0, 3], [0]],
)
BS = _plan(
    "bubble",
    [
        "bubble sort swaps adjacent items until the list is sorted at last",
        "it is slow on large inputs",
    ],
    [[0, 4, 8], [0]],
)


def _assert_only(contents: list[str], wanted: str, unwanted: str) -> None:
    assert contents, "nothing was assembled"
    for c in contents:
        assert wanted in c, c
        assert unwanted not in c, c


class TestDifferentPlansNeverShareArtifacts:
    def test_second_plan_ships_its_own_video_and_narration(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        out = fakes.run(monkeypatch, BS, "bubble sort")

        # Every clip of the second run carries bubble-sort render AND audio.
        _assert_only(out, "bubble", "gradient")
        assert len(out) == 4  # 3 cues + 1 cue

    def test_same_topic_replanned_does_not_reuse_render(self, fakes, monkeypatch):
        # A fresh run of the same topic plans again; a different plan must not
        # inherit the old render just because the topic string matches.
        fakes.run(monkeypatch, GD, "gradient descent")
        replanned = _plan(
            "regradient",
            [
                "gradient descent walks downhill step by step",
                "momentum helps a lot",
            ],
            [[0, 3], [0]],
        )
        out = fakes.run(monkeypatch, replanned, "gradient descent")
        assert all("regradient" in c for c in out), out

    def test_more_cues_remuxes_every_cue(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        grown = json.loads(json.dumps(GD))
        grown["sections"][0]["cue_word_indices"] = [0, 2, 4]
        grown["sections"][0]["cues"].append({"index": 2, "visual": "grown"})
        before = fakes.mux_calls
        out = fakes.run(monkeypatch, grown, "gradient descent")
        assert len(out) == 4
        # Section 1 (3 cues) re-muxed in full; section 2 unchanged, reused.
        assert fakes.mux_calls - before == 3

    def test_changed_narration_reslices_audio(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        changed = json.loads(json.dumps(GD))
        changed["sections"][1]["narration"] = "the step size matters a great deal"
        out = fakes.run(monkeypatch, changed, "gradient descent")
        assert "step size" in out[-1]
        assert "learning rate" not in out[-1]

    def test_fewer_cues_leaves_no_stale_cue_files(self, fakes, monkeypatch):
        # Stale higher-numbered cue clips would otherwise linger in the shared
        # muxed folder and show up in manimgen-edit next to the new plan.
        fakes.run(monkeypatch, BS, "bubble sort")
        fakes.run(monkeypatch, GD, "gradient descent")
        names = sorted(os.listdir(fakes.dirs["muxed"]))
        assert not any(n.startswith("section_01_cue02") for n in names), names

    def test_pdf_edited_in_place_is_stale(self, fakes, monkeypatch, tmp_path):
        # Same path, different bytes: even an identical plan must re-render,
        # because the key hashes the PDF's bytes, not its path.
        pdf = tmp_path / "notes.pdf"
        pdf.write_bytes(b"%PDF-1.4 first draft")
        fakes.run(monkeypatch, GD, "", pdf=str(pdf))
        codegen = fakes.codegen_calls
        pdf.write_bytes(b"%PDF-1.4 second draft")
        fakes.run(monkeypatch, GD, "", pdf=str(pdf))
        assert fakes.codegen_calls == codegen + 2


class TestSameContentIsReused:
    def test_resume_reuses_everything(self, fakes, monkeypatch):
        first = fakes.run(monkeypatch, GD, "gradient descent")
        codegen, muxes = fakes.codegen_calls, fakes.mux_calls
        again = fakes.run(monkeypatch, None, "gradient descent", resume=True)
        assert again == first
        assert fakes.codegen_calls == codegen
        assert fakes.mux_calls == muxes

    def test_same_plan_with_lost_muxed_clips_reuses_render(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        for name in os.listdir(fakes.dirs["muxed"]):
            os.remove(os.path.join(fakes.dirs["muxed"], name))
        codegen = fakes.codegen_calls
        out = fakes.run(monkeypatch, None, "gradient descent", resume=True)
        assert len(out) == 3
        assert fakes.codegen_calls == codegen


class TestPartialSidecars:
    def test_missing_muxed_sidecar_is_stale(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        os.remove(os.path.join(fakes.dirs["muxed"], "section_01_cue01.mp4.hash"))
        muxes = fakes.mux_calls
        fakes.run(monkeypatch, None, "gradient descent", resume=True)
        assert fakes.mux_calls - muxes == 2  # whole section re-muxed

    def test_truncated_render_sidecar_is_stale(self, fakes, monkeypatch):
        fakes.run(monkeypatch, GD, "gradient descent")
        sidecar = os.path.join(fakes.dirs["videos"], "Section01Scene.mp4.hash")
        key = _read(sidecar)
        _write(sidecar, key[:4])
        for name in os.listdir(fakes.dirs["muxed"]):
            os.remove(os.path.join(fakes.dirs["muxed"], name))
        codegen = fakes.codegen_calls
        fakes.run(monkeypatch, None, "gradient descent", resume=True)
        assert fakes.codegen_calls == codegen + 1


class TestEditorListing:
    def test_editor_lists_current_plan_clips_only(self, fakes, monkeypatch):
        from pathlib import Path

        from manimgen.editor import server

        fakes.run(monkeypatch, BS, "bubble sort")
        fakes.run(monkeypatch, GD, "gradient descent")
        monkeypatch.setattr(server, "VIDEOS_DIR", Path(fakes.dirs["muxed"]))
        monkeypatch.setattr(server, "_probe_duration", lambda p: 1.0)
        names = [c["filename"] for c in server._get_clips()]
        muxed = [n for n in names if not n.endswith("_video.mp4")]
        # Sidecars are not clips, and BS's third cue of section 1 is gone.
        assert muxed == [
            "section_01_cue00.mp4",
            "section_01_cue01.mp4",
            "section_02_cue00.mp4",
        ]
