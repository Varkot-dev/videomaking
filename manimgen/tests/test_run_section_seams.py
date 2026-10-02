"""Tests for the _run_section seam extraction (#31).

_run_section was a 160+ line function doing six jobs; the render-cache
fast-path bypassed the timing freeze gate the codegen path ran. #31 decomposes
it into three pure-ish seams without changing behavior:

  _generate_and_gate  → GateResult        (codegen + timing gate)
  _render_with_retry  → (success, path)    (render + validate + retry + fallback)
  _cut_and_mux        → [clip paths]       (cut + per-cue mux)

These tests pin the contract that makes the cache-bypass fix real: a timing
block detected by the gate causes the expensive first render to be SKIPPED, and
both the cache path and the render path route their freeze check through the
single _code_blocking_freezes seam (so they can never drift).

Zero LLM calls, zero subprocess calls — every external call is monkeypatched.
"""

import logging

from manimgen import cli
from manimgen.types import GateResult


class TestGenerateAndGate:
    def test_returns_gate_result(self, monkeypatch):
        monkeypatch.setattr(
            cli, "generate_scenes", lambda *a, **k: ("CODE", "Demo", "/tmp/d.py")
        )
        # No cue_durations → timing gate is skipped entirely.
        gate = cli._generate_and_gate({"id": "section_01"}, None, None)
        assert isinstance(gate, GateResult)
        assert gate.code == "CODE"
        assert gate.class_name == "Demo"
        assert gate.scene_path == "/tmp/d.py"
        assert gate.timing_blocked is False

    def test_clean_timing_does_not_block(self, monkeypatch):
        monkeypatch.setattr(
            cli, "generate_scenes", lambda *a, **k: ("CODE", "Demo", "/tmp/d.py")
        )
        # apply_timing_gate lives in retry; patch it there (imported inside seam).
        import manimgen.validator.retry as retry

        monkeypatch.setattr(
            retry, "apply_timing_gate", lambda code, path, durs: (code, [])
        )
        gate = cli._generate_and_gate({"id": "section_01"}, [3.0], None)
        assert gate.timing_blocked is False

    def test_unresolvable_timing_sets_block(self, monkeypatch):
        monkeypatch.setattr(
            cli, "generate_scenes", lambda *a, **k: ("CODE", "Demo", "/tmp/d.py")
        )
        import manimgen.validator.retry as retry

        monkeypatch.setattr(
            retry,
            "apply_timing_gate",
            lambda code, path, durs: (code, ["cue 0: 5.0s freeze tail"]),
        )
        gate = cli._generate_and_gate({"id": "section_01"}, [3.0], None)
        assert gate.timing_blocked is True


class TestRenderWithRetrySkipsBlockedRender:
    def test_timing_blocked_skips_first_render(self, monkeypatch):
        """A timing-blocked gate must NOT call run_scene — it routes to retry."""
        calls = {"run_scene": 0, "retry": 0}

        def _run_scene(scene_path, class_name):
            calls["run_scene"] += 1
            return True, "/tmp/v.mp4"

        def _retry(section, code, cls, path, cue_durations=None):
            calls["retry"] += 1
            return True, "/tmp/retried.mp4"

        monkeypatch.setattr(cli, "run_scene", _run_scene)
        monkeypatch.setattr(cli, "retry_scene", _retry)

        gate = GateResult(
            code="CODE",
            class_name="Demo",
            scene_path="/tmp/d.py",
            timing_blocked=True,
        )
        log = logging.getLogger("test")
        success, path = cli._render_with_retry({}, gate, [3.0], log)

        assert calls["run_scene"] == 0  # expensive render skipped
        assert calls["retry"] == 1  # routed straight to retry
        assert success is True
        assert path == "/tmp/retried.mp4"

    def test_not_blocked_runs_first_render(self, monkeypatch):
        calls = {"run_scene": 0, "retry": 0}

        monkeypatch.setattr(
            cli,
            "run_scene",
            lambda p, c: (calls.__setitem__("run_scene", 1), (True, "/tmp/v.mp4"))[1],
        )
        monkeypatch.setattr(
            cli,
            "retry_scene",
            lambda *a, **k: (calls.__setitem__("retry", 1), (True, "x"))[1],
        )
        # validate_render is imported inside the seam from render_validator.
        import manimgen.validator.render_validator as rv
        from manimgen.validator.render_validator import ValidationResult

        monkeypatch.setattr(
            rv,
            "validate_render",
            lambda *a, **k: ValidationResult(ok=True, issues=[], severity="none"),
        )
        # No freeze on the clean render.
        monkeypatch.setattr(cli, "_code_blocking_freezes", lambda code, durs: [])

        gate = GateResult(
            code="CODE", class_name="Demo", scene_path="/tmp/d.py", timing_blocked=False
        )
        success, path = cli._render_with_retry({}, gate, [3.0], logging.getLogger("t"))

        assert calls["run_scene"] == 1
        assert calls["retry"] == 0  # clean render → no retry
        assert success is True
        assert path == "/tmp/v.mp4"

    def test_post_render_freeze_forces_retry(self, monkeypatch):
        """A real freeze on the first render routes into retry (cache-bypass fix
        and render path share the same _code_blocking_freezes seam)."""
        calls = {"retry": 0}
        monkeypatch.setattr(cli, "run_scene", lambda p, c: (True, "/tmp/v.mp4"))
        monkeypatch.setattr(
            cli,
            "retry_scene",
            lambda *a, **k: (calls.__setitem__("retry", 1), (True, "/tmp/r.mp4"))[1],
        )
        import manimgen.validator.render_validator as rv
        from manimgen.validator.render_validator import ValidationResult

        monkeypatch.setattr(
            rv,
            "validate_render",
            lambda *a, **k: ValidationResult(ok=True, issues=[], severity="none"),
        )
        # Shared seam reports a blocking freeze → must force the retry path.
        monkeypatch.setattr(
            cli, "_code_blocking_freezes", lambda code, durs: ["cue 0: 4.0s freeze"]
        )

        gate = GateResult(
            code="CODE", class_name="Demo", scene_path="/tmp/d.py", timing_blocked=False
        )
        success, path = cli._render_with_retry({}, gate, [3.0], logging.getLogger("t"))

        assert calls["retry"] == 1
        assert path == "/tmp/r.mp4"


class TestSharedFreezeSeam:
    def test_cache_path_uses_same_seam_as_render(self, monkeypatch, tmp_path):
        """_cached_scene_blocking_freezes routes through _code_blocking_freezes —
        the exact same gate the render path uses, so they cannot drift."""
        from manimgen import paths

        scene_dir = tmp_path / "scenes"
        scene_dir.mkdir()
        (scene_dir / "section_01.py").write_text("# scene\n", encoding="utf-8")
        monkeypatch.setattr(paths, "scenes_dir", lambda: str(scene_dir))

        seen = {}

        def _spy(code, durs):
            seen["called"] = True
            return ["cue 0: 6.0s freeze tail"]

        monkeypatch.setattr(cli, "_code_blocking_freezes", _spy)

        out = cli._cached_scene_blocking_freezes({"id": "section_01"}, [10.0])
        assert seen.get("called") is True
        assert out == ["cue 0: 6.0s freeze tail"]


# ---------------------------------------------------------------------------
# R02: a precheck-blocked first draft must enter the retry path, not abort the run
# ---------------------------------------------------------------------------

# Item assignment into a real VGroup is still a render-blocking finding after R13.
_BLOCKED_DRAFT = (
    "from manimlib import *\n\n\n"
    "class Section01Scene(Scene):\n"
    "    def construct(self):\n"
    "        boxes = VGroup(*[Square() for _ in range(3)])\n"
    "        boxes[0] = Circle()\n"
    "        self.wait(1.0)\n"
)
_CLEAN_DRAFT = (
    "from manimlib import *\n\n\n"
    "class Section01Scene(Scene):\n"
    "    def construct(self):\n"
    "        self.wait(1.0)\n"
)
_SECTION = {
    "id": "section_01",
    "title": "Sorting",
    "narration": "Swap two items.",
    "cues": [{"index": 0, "visual": "Swap two bars."}],
}


class TestPrecheckBlockedFirstDraft:
    def _wire(self, monkeypatch, tmp_path, draft, fake_retry=True):
        """Real generate_scenes and codeguard; only the LLM, disk layout and the
        render/retry/fallback leaves are faked."""
        from manimgen import paths
        from manimgen.generator import scene_generator

        monkeypatch.setattr(scene_generator, "chat", lambda **k: draft)
        # Prompt and example files are read with the platform default encoding;
        # stub those reads so the test does not depend on the locale.
        monkeypatch.setattr(scene_generator, "_load_director_prompt", lambda: "sys")
        monkeypatch.setattr(scene_generator, "_load_examples_text", lambda s: "")
        monkeypatch.setattr(paths, "scenes_dir", lambda: str(tmp_path))
        monkeypatch.setattr(paths, "logs_dir", lambda: str(tmp_path / "logs"))
        monkeypatch.setattr(cli, "_find_rendered_video", lambda cls: None)
        calls = {"run_scene": 0, "retry": 0, "fallback": 0}

        def _run_scene(path, cls):
            calls["run_scene"] += 1
            return True, "/tmp/first.mp4"

        def _fallback(section):
            calls["fallback"] += 1
            return "/tmp/fallback.mp4"

        monkeypatch.setattr(cli, "run_scene", _run_scene)
        monkeypatch.setattr(cli, "fallback_scene", _fallback)
        if fake_retry:

            def _retry(section, code, cls, path, cue_durations=None):
                calls["retry"] += 1
                return True, "/tmp/retried.mp4"

            monkeypatch.setattr(cli, "retry_scene", _retry)
        return calls

    def test_gate_reports_precheck_blocked_instead_of_raising(
        self, monkeypatch, tmp_path
    ):
        self._wire(monkeypatch, tmp_path, _BLOCKED_DRAFT)
        gate = cli._generate_and_gate(dict(_SECTION), None, None)
        assert gate.precheck_blocked is True
        assert gate.timing_blocked is False
        assert gate.class_name == "Section01Scene"
        # The doomed file is on disk for retry_scene to repair.
        with open(gate.scene_path, encoding="utf-8") as f:
            assert "boxes[0] = Circle()" in f.read()
        assert gate.code == open(gate.scene_path, encoding="utf-8").read()

    def test_run_section_goes_through_retry_and_does_not_raise(
        self, monkeypatch, tmp_path
    ):
        calls = self._wire(monkeypatch, tmp_path, _BLOCKED_DRAFT)
        out = cli._run_section(dict(_SECTION), 1, False, "hash")
        assert calls["run_scene"] == 0, "the doomed first render must be skipped"
        assert calls["retry"] == 1
        assert calls["fallback"] == 0
        assert out == ["/tmp/retried.mp4"]

    def test_run_section_falls_back_when_retry_fails(self, monkeypatch, tmp_path):
        calls = self._wire(monkeypatch, tmp_path, _BLOCKED_DRAFT)
        monkeypatch.setattr(cli, "retry_scene", lambda *a, **k: (False, None))
        out = cli._run_section(dict(_SECTION), 1, False, "hash")
        assert calls["run_scene"] == 0
        assert out == ["/tmp/fallback.mp4"]

    def test_clean_draft_renders_on_the_first_pass(self, monkeypatch, tmp_path):
        calls = self._wire(monkeypatch, tmp_path, _CLEAN_DRAFT)
        import manimgen.validator.render_validator as rv
        from manimgen.validator.render_validator import ValidationResult

        monkeypatch.setattr(
            rv,
            "validate_render",
            lambda *a, **k: ValidationResult(ok=True, issues=[], severity="none"),
        )
        gate = cli._generate_and_gate(dict(_SECTION), None, None)
        assert gate.precheck_blocked is False
        out = cli._run_section(dict(_SECTION), 1, False, "hash")
        assert calls["run_scene"] == 1
        assert calls["retry"] == 0
        assert out == ["/tmp/first.mp4"]

    def test_real_retry_scene_gets_the_precheck_stderr_within_budget(
        self, monkeypatch, tmp_path
    ):
        """Through the real retry_scene: the blocked draft's first attempt is the
        precheck failure, the LLM fix receives its stderr, and only the existing
        per-section and per-run budgets are spent."""
        import manimgen.validator.retry as retry

        calls = self._wire(monkeypatch, tmp_path, _BLOCKED_DRAFT, fake_retry=False)
        renders = []

        def _no_render(cmd, *a, **k):
            renders.append(cmd)
            return None, "", "", True  # timed out

        monkeypatch.setattr(retry, "_load_retry_system_prompt", lambda: "sys")
        monkeypatch.setattr("manimgen.procutil.run_tree", _no_render)
        retry.reset_run_budget()
        prompts = []

        def _fix(system, user, **k):
            prompts.append(user)
            return _CLEAN_DRAFT

        monkeypatch.setattr(retry, "chat", _fix)

        out = cli._run_section(dict(_SECTION), 1, False, "hash")

        assert calls["run_scene"] == 0
        assert out == ["/tmp/fallback.mp4"]
        assert prompts, "the LLM fix must run for a blocked draft"
        assert "item assignment" in prompts[0]
        assert len(prompts) <= retry.MAX_ERROR_LLM_FIX_CALLS
        assert retry.run_budget_used() == len(prompts)
