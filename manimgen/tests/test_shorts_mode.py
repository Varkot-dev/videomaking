"""Vertical shorts mode (`manimgen --short`).

Covers the format switch (render flags, resolution, prompts, fallback card),
the single-idea planner, and the CLI wiring. The vertical reference scene
embedded in the Director prompt must itself pass every static check, because
the model copies its layout.
"""

import os
import re

import pytest

import manimgen
from manimgen import cli, paths
from manimgen.generator import scene_generator
from manimgen.planner import lesson_planner as lp
from manimgen.validator import fallback, render_command
from manimgen.validator.codeguard import precheck_and_autofix
from manimgen.validator.scene_ast_gate import inspect_scene_code
from manimgen.validator.timing_verifier import verify_timing

_PKG = os.path.dirname(manimgen.__file__)
_VERTICAL = os.path.join(_PKG, "generator", "prompts", "director_vertical.md")

_PLAN_REPLY = (
    '{"title": "Why 3-4-5 works", "sections": ['
    '{"id": "section_01", "title": "The rope trick", '
    '"narration": "Why does a three four five rope make a square corner? '
    '[CUE] Build a square on each side. [CUE] Nine plus sixteen is twenty five.", '
    '"cues": ['
    '{"index": 0, "visual": "Technique: fade_reveal. A triangle."}, '
    '{"index": 1, "visual": "Technique: stagger_reveal. Three squares."}, '
    '{"index": 2, "visual": "Technique: equation_morph. 9 + 16 = 25."}]}, '
    '{"id": "section_02", "title": "Extra", "narration": "Should be dropped.", '
    '"cues": [{"index": 0, "visual": "Technique: fade_reveal. x"}]}]}'
)


@pytest.fixture
def short_format():
    paths.set_video_format("short")
    yield


@pytest.mark.unit
class TestFormatSwitch:
    def test_landscape_is_the_default_and_uses_the_quality_flag(self):
        cmd = render_command.build_manimgl_command("s.py", "S")
        assert paths.video_format() == "landscape"
        assert paths.render_quality_flag() in cmd and "-r" not in cmd

    def test_short_renders_1080x1920_with_no_landscape_quality_flag(self, short_format):
        cmd = render_command.build_manimgl_command("s.py", "S")
        assert cmd[cmd.index("-r") + 1] == "1080x1920"
        assert not {"-l", "-m", "--hd", "--uhd"} & set(cmd)
        assert "--fps" not in cmd
        assert paths.render_resolution() == "1080x1920"

    def test_unknown_format_is_refused(self):
        with pytest.raises(ValueError):
            paths.set_video_format("square")

    def test_assembler_scales_to_the_vertical_frame(self, short_format):
        from manimgen.renderer import assembler

        assert assembler._vf_scale().startswith("scale=1080:1920,")


@pytest.mark.unit
class TestVerticalDirectorPrompt:
    def test_vertical_rules_are_appended_only_for_shorts(self):
        assert "VERTICAL SHORT MODE" not in scene_generator._load_director_prompt()
        paths.set_video_format("short")
        prompt = scene_generator._load_director_prompt()
        assert prompt.rstrip().endswith("```")  # vertical rules come last
        assert "VERTICAL SHORT MODE" in prompt and "4.5 units wide" in prompt

    def test_landscape_examples_and_frames_are_not_sent_for_a_short(self, monkeypatch):
        sent = {}

        def chat(system, user, images=None, role=None):
            sent.update(user=user, images=images)
            return "from manimlib import *\n\nclass Section01Scene(Scene):\n    def construct(self):\n        self.wait(1)\n"

        monkeypatch.setattr(scene_generator, "chat", chat)
        monkeypatch.setattr(
            scene_generator, "load_reference_frames", lambda: ["landscape-frame"]
        )
        monkeypatch.setattr(
            scene_generator, "_load_examples_text", lambda s: "LANDSCAPE EXAMPLE"
        )
        monkeypatch.setitem(paths._PATHS, "scenes", str(self._tmp))
        section = {"id": "section_01", "title": "T", "narration": "one two", "cues": []}
        paths.set_video_format("short")
        scene_generator.generate_scenes(section, cue_durations=[1.0])
        assert "LANDSCAPE EXAMPLE" not in sent["user"] and not sent["images"]
        paths.set_video_format("landscape")
        scene_generator.generate_scenes(section, cue_durations=[1.0])
        assert "LANDSCAPE EXAMPLE" in sent["user"] and sent["images"]

    @pytest.fixture(autouse=True)
    def _tmp_dir(self, tmp_path):
        self._tmp = tmp_path

    def test_reference_scene_passes_every_static_check(self):
        with open(_VERTICAL, encoding="utf-8") as f:
            code = re.search(r"```python\n(.*?)```", f.read(), re.DOTALL).group(1)
        assert inspect_scene_code(code).ok
        fixed = precheck_and_autofix(code)
        assert fixed == code, "codeguard would rewrite the reference scene"
        result = verify_timing(code, [3.0, 3.0, 3.5])  # cue lengths in the comments
        assert result["ok"] and len(result["cues"]) == 3

    def test_reference_scene_stays_inside_the_safe_box(self):
        with open(_VERTICAL, encoding="utf-8") as f:
            text = f.read()
        ys = [float(y) for y in re.findall(r"move_to\(\[CX, (-?[\d.]+), 0\]\)", text)]
        assert ys and all(-2.0 <= y <= 2.8 for y in ys)


@pytest.mark.unit
class TestFallbackCard:
    def test_short_card_is_gate_clean_and_width_guarded(self, short_format):
        code = fallback._fallback_code(
            "XFallbackScene", "01", "A title", "A subtitle", 5
        )
        assert inspect_scene_code(code).ok
        assert "set_width(3.3)" in code and "to_edge" not in code

    def test_landscape_card_is_unchanged(self):
        code = fallback._fallback_code(
            "XFallbackScene", "01", "A title", "A subtitle", 5
        )
        assert "to_edge(UP" in code


@pytest.mark.integration
class TestPlanShort:
    @pytest.fixture
    def llm(self, monkeypatch):
        calls = []
        monkeypatch.setattr(lp, "research_topic", lambda topic: {})

        def chat(system, user, images=None, json_mode=False, role=None):
            calls.append({"system": system, "user": user, "role": role})
            return _PLAN_REPLY

        monkeypatch.setattr(lp, "chat", chat)
        return calls

    def test_plan_has_exactly_one_section_and_is_tagged(self, llm):
        plan = lp.plan_short("the Pythagorean theorem", archetype="misconception")
        assert len(plan["sections"]) == 1
        assert plan["_format"] == "short" and plan["_archetype"] == "misconception"
        section = plan["sections"][0]
        assert "[CUE]" not in section["narration"]
        assert len(section["cues"]) == len(section["cue_word_indices"]) == 3

    def test_the_owners_angle_and_voice_reach_the_model(self, llm):
        lp.plan_short(
            "limits",
            archetype="worked",
            confusion="I thought a limit was just plugging in",
            insight="it is about what you approach",
            voice_profile="short sentences, says 'you'",
        )
        user = llm[0]["user"]
        assert "FORMAT: worked" in user
        assert "I thought a limit was just plugging in" in user
        assert "it is about what you approach" in user
        assert "short sentences, says 'you'" in user

    def test_technique_menu_has_no_3d_or_side_by_side(self, llm):
        lp.plan_short("x")
        system = llm[0]["system"]
        menu = system[system.index("using one of:") :].split("\n", 1)[0]
        assert "`fade_reveal`" in menu
        assert "3d_surface" not in menu and "split_screen" not in menu
        assert "{{TECHNIQUES}}" not in system

    def test_unknown_format_is_refused_before_any_llm_call(self, llm):
        with pytest.raises(ValueError):
            lp.plan_short("x", archetype="listicle")
        assert llm == []


@pytest.mark.integration
class TestCli:
    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        from manimgen.types import SectionOutcome, SectionStatus

        seen = {}
        monkeypatch.setattr(cli, "_PLAN_CACHE", str(tmp_path / "plan.json"))
        monkeypatch.setitem(paths._PATHS, "videos", str(tmp_path / "videos"))
        monkeypatch.setitem(paths._SHORTS, "voice_profile", str(tmp_path / "voice.md"))
        monkeypatch.setattr(cli, "_load_config", lambda: {})

        def plan_short(topic, **kwargs):
            seen["plan_short"] = {"topic": topic, **kwargs}
            return {
                "title": "T",
                "_format": "short",
                "sections": [{"id": "section_01", "title": "S", "narration": "n"}],
            }

        def run_section(*a, **k):
            seen["format_at_render"] = paths.video_format()
            return SectionOutcome(SectionStatus.OK, ["clip.mp4"])

        out = tmp_path / "final.mp4"
        out.write_bytes(b"x")
        monkeypatch.setattr(cli, "plan_short", plan_short)
        monkeypatch.setattr(cli, "plan_lesson", lambda t: pytest.fail("long plan used"))
        monkeypatch.setattr(cli, "_run_section", run_section)
        monkeypatch.setattr(cli, "assemble_video", lambda clips, title: str(out))

        def go(*argv):
            monkeypatch.setattr("sys.argv", ["manimgen", *argv])
            try:
                cli.main()
            except SystemExit as e:
                return e.code, seen
            return 0, seen

        return go, tmp_path

    def test_short_plans_and_renders_in_the_vertical_format(self, run):
        go, tmp_path = run
        (tmp_path / "voice.md").write_text("I talk plainly.", encoding="utf-8")
        code, seen = go(
            "limits",
            "--short",
            "--format",
            "worked",
            "--confused",
            "plugging in",
            "--clicked",
            "approaching",
        )
        assert code in (0, None)
        assert seen["format_at_render"] == "short"
        assert seen["plan_short"] == {
            "topic": "limits",
            "archetype": "worked",
            "confusion": "plugging in",
            "insight": "approaching",
            "voice_profile": "I talk plainly.",
        }

    def test_missing_voice_profile_is_fine(self, run):
        go, _ = run
        _code, seen = go("limits", "--short")
        assert seen["plan_short"]["voice_profile"] == ""

    def test_resume_keeps_the_short_format(self, run):
        go, _ = run
        go("limits", "--short")
        paths.set_video_format("landscape")
        _code, seen = go("--resume")
        assert seen["format_at_render"] == "short"

    def test_short_with_a_pdf_is_a_usage_error(self, run, capsys):
        go, _ = run
        code, _ = go("--short", "--pdf", "notes.pdf")
        assert code == 2


@pytest.mark.unit
class TestDraftAndFast:
    def test_draft_renders_small_in_both_formats(self):
        paths.set_draft(True)
        assert paths.render_quality_flag() == "-m"
        assert paths.render_resolution() == "1280x720"
        paths.set_video_format("short")
        cmd = render_command.build_manimgl_command("s.py", "S")
        assert cmd[cmd.index("-r") + 1] == "540x960"
        assert paths.render_resolution() == "540x960"

    def test_draft_and_final_never_share_a_cache_key(self):
        final = cli._content_hash("topic", {})
        paths.set_draft(True)
        assert cli._content_hash("topic", {}) != final

    def test_fast_skips_the_research_and_critic_calls(self, monkeypatch):
        calls = []

        def chat(system, user, images=None, json_mode=False, role=None):
            calls.append(role)
            return _PLAN_REPLY

        monkeypatch.setattr(lp, "chat", chat)
        lp.set_fast(True)
        plan = lp.plan_lesson("the Pythagorean theorem")
        assert calls == ["planner"]
        assert plan["sections"]

    def test_normal_mode_still_runs_research_and_critic(self, monkeypatch):
        calls = []

        def chat(system, user, images=None, json_mode=False, role=None):
            calls.append(role)
            return _PLAN_REPLY

        monkeypatch.setattr(lp, "chat", chat)
        lp.plan_lesson("the Pythagorean theorem")
        assert calls[0] == "researcher" and "planner" in calls and "critic" in calls
