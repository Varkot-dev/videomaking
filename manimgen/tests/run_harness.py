"""Shared fakes for tests that drive cli.main() through the real section pipeline.

Only the leaf seams are faked: the planner and Director LLM calls, edge-tts,
the manimgl render subprocess and the ffmpeg calls. Each fake writes a text
"media" file that records where its content came from, so the clip list handed
to the assembler shows exactly which plan's narration and video ship.
"""

import json
import os
import sys
from types import SimpleNamespace

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
            lines.append(f"# CUE {i}: {d:.2f}s")
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
