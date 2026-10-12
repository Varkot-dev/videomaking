"""Tests for llm.chat(role=...) model routing (R27). Nothing is spawned."""

import ast
import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import manimgen.llm as llm_mod
from manimgen.llm import chat

_ROLES = (
    "researcher",
    "planner",
    "planner_pdf",
    "critic",
    "cue_refill",
    "director",
    "error_fix",
    "visual_fix",
    "layout_check",
)


def _result_line(text="ok", is_error=False, subtype="success"):
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": text}
    )


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout.encode(), stderr=stderr.encode()
    )


def _cli_cmd(role=None):
    """Run chat() through a mocked subprocess and return the argv it built."""
    with patch.object(
        llm_mod, "_run_cli", return_value=_completed(_result_line("ok"))
    ) as run:
        chat(system="s", user="u", role=role)
    return run.call_args.args[0]


def _model(cmd):
    return cmd[cmd.index("--model") + 1]


class TestRoleRouting:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
        monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
        for r in _ROLES:
            monkeypatch.delenv(f"MANIMGEN_MODEL_{r.upper()}", raising=False)
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {})
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "claude_cli_model", "sonnet")

    def test_default_keeps_every_role_on_claude_cli_model(self):
        assert _model(_cli_cmd()) == "sonnet"
        for role in _ROLES:
            assert _model(_cli_cmd(role)) == "sonnet"

    def test_shipped_config_changes_no_default(self):
        assert llm_mod._load_llm_config()["models"] == {}
        assert llm_mod._load_llm_config()["claude_cli_model"] == "sonnet"

    def test_config_role_maps_to_model_flag(self, monkeypatch):
        monkeypatch.setitem(
            llm_mod._LLM_CONFIG, "models", {"director": "opus", "layout_check": "haiku"}
        )
        assert _model(_cli_cmd("director")) == "opus"
        assert _model(_cli_cmd("layout_check")) == "haiku"
        assert _model(_cli_cmd("critic")) == "sonnet"
        assert _model(_cli_cmd(None)) == "sonnet"

    def test_env_override_beats_config(self, monkeypatch):
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"director": "opus"})
        monkeypatch.setenv("MANIMGEN_MODEL_DIRECTOR", "claude-haiku-4-5")
        assert _model(_cli_cmd("director")) == "claude-haiku-4-5"

    def test_env_override_without_config_entry(self, monkeypatch):
        monkeypatch.setenv("MANIMGEN_MODEL_CUE_REFILL", "haiku")
        assert _model(_cli_cmd("cue_refill")) == "haiku"

    def test_unknown_role_falls_back_never_errors(self, monkeypatch):
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"director": "opus"})
        assert _model(_cli_cmd("no_such_role")) == "sonnet"

    def test_role_is_case_insensitive(self, monkeypatch):
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"director": "opus"})
        assert _model(_cli_cmd("Director")) == "opus"

    def test_bad_values_are_ignored(self, monkeypatch):
        monkeypatch.setitem(
            llm_mod._LLM_CONFIG, "models", {"director": "--dangerous", "critic": ""}
        )
        assert _model(_cli_cmd("director")) == "sonnet"
        assert _model(_cli_cmd("critic")) == "sonnet"
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", ["not", "a", "dict"])
        assert _model(_cli_cmd("director")) == "sonnet"

    def test_config_loader_normalizes_models_block(self, tmp_path, monkeypatch):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "llm:\n  models:\n    Director: opus\n    critic: ''\n    x: 3\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(llm_mod, "_CONFIG_PATH", str(cfg))
        assert llm_mod._load_llm_config()["models"] == {"director": "opus", "x": "3"}

    def test_chat_without_role_passes_helper_args_unchanged(self):
        with patch("manimgen.llm._claude_cli", return_value="hi") as mock_cli:
            chat(system="s", user="u")
        mock_cli.assert_called_once_with("s", "u", [])

    def test_role_reaches_anthropic(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("MANIMGEN_ALLOW_PAID_API", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"critic": "claude-x"})
        fake = MagicMock()
        msg = MagicMock(content=[MagicMock(type="text", text="t")], stop_reason="end")
        fake.Anthropic.return_value.messages.create.return_value = msg
        with patch.dict("sys.modules", {"anthropic": fake}):
            chat(system="s", user="u", role="critic")
        create = fake.Anthropic.return_value.messages.create
        assert create.call_args.kwargs["model"] == "claude-x"

    def test_role_reaches_ollama(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"critic": "qwen"})
        monkeypatch.setattr(llm_mod, "_validate_ollama_url", lambda u: u)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"message": {"content": "x"}}
        with patch("requests.post", return_value=resp) as post:
            chat(system="s", user="u", role="critic")
        assert post.call_args.kwargs["json"]["model"] == "qwen"

    def test_every_call_site_passes_a_known_role(self):
        base = os.path.join(os.path.dirname(__file__), "..", "manimgen")
        files = [
            "planner/lesson_planner.py",
            "generator/scene_generator.py",
            "validator/retry.py",
            "validator/layout_checker.py",
        ]
        roles = set()
        for rel in files:
            with open(os.path.join(base, rel), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                is_chat = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "chat"
                )
                if not is_chat:
                    continue
                kw = {k.arg: k.value for k in node.keywords}
                assert "role" in kw, f"{rel}:{node.lineno}: chat() without role="
                if isinstance(kw["role"], ast.Constant):
                    assert kw["role"].value in _ROLES
                    roles.add(kw["role"].value)
        assert {"error_fix", "visual_fix", "layout_check", "director"} <= roles
        assert {"critic", "cue_refill", "researcher"} <= roles


class _Stop(BaseException):
    """Ends a planner call right after chat() was reached (BaseException so a
    broad `except Exception` in the planner cannot swallow it)."""


class TestPlannerRoles:
    """Each planner chat() call labels itself, so it is routed and logged."""

    @pytest.fixture(autouse=True)
    def _no_prompt_files(self, monkeypatch):
        # Prompt loading is not what these tests are about, and the planner
        # reads those files with the platform default encoding.
        from manimgen.planner import lesson_planner as lp

        for name in (
            "_load_system_prompt",
            "_load_pdf_system_prompt",
            "_load_researcher_system_prompt",
            "_load_critic_system_prompt",
        ):
            monkeypatch.setattr(lp, name, lambda: "system")

    @staticmethod
    def _recorder(stop_after):
        roles = []

        def fake_chat(**kwargs):
            roles.append(kwargs["role"])
            if len(roles) >= stop_after:
                raise _Stop
            return "{}"

        return roles, fake_chat

    def test_topic_planning_uses_researcher_then_planner(self):
        from manimgen.planner import lesson_planner as lp

        roles, fake_chat = self._recorder(stop_after=2)
        with patch.object(lp, "chat", fake_chat), pytest.raises(_Stop):
            lp.plan_lesson("binary search")
        assert roles == ["researcher", "planner"]

    def test_pdf_planning_uses_the_pdf_planner_role(self):
        from manimgen.planner import lesson_planner as lp

        parsed = {"raw_text": "x", "images": [], "extracted_pages": 1, "chunks": ["x"]}
        roles, fake_chat = self._recorder(stop_after=1)
        with (
            patch("manimgen.input.pdf_parser.parse_pdf", return_value=parsed),
            patch.object(lp, "chat", fake_chat),
            pytest.raises(_Stop),
        ):
            lp.plan_lesson_from_pdf("notes.pdf")
        assert roles == ["planner_pdf"]

    def test_storyboard_critic_uses_the_critic_role(self):
        from manimgen.planner import lesson_planner as lp

        roles, fake_chat = self._recorder(stop_after=1)
        with patch.object(lp, "chat", fake_chat), pytest.raises(_Stop):
            lp._self_correct({"sections": []})
        assert roles == ["critic"]


def test_ledger_records_the_routed_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setattr(llm_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setitem(llm_mod._LLM_CONFIG, "models", {"director": "opus"})
    monkeypatch.delenv("MANIMGEN_MODEL_DIRECTOR", raising=False)
    _cli_cmd("director")
    path = os.path.join(llm_mod._ledger_dir(), "llm_usage.jsonl")
    with open(path, encoding="utf-8") as f:
        assert json.loads(f.readline())["model"] == "opus"
