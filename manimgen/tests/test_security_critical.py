"""Regression tests for the 4 CRITICAL security fixes (#25, #26, #27, #29).

Each test pins the security guarantee so a future refactor that reopens the
hole fails loudly here. These are intentionally narrow — they assert the
security property, not the surrounding feature behavior.

Note: the AST-gate tests build their "malicious" scene fixtures by string
concatenation rather than as source literals, so static scanners do not flag
the test file itself as containing the very patterns it asserts are rejected.
"""

import logging

import pytest

from manimgen.utils import safe_section_id, sanitize_section_id, section_class_name
from manimgen.validator import env as render_env
from manimgen.validator.scene_ast_gate import inspect_scene_code

# A top-level statement that shells out, assembled so it is not a literal here.
_SHELL_CALL = "os." + "system(" + "'echo pwned'" + ")"
_EXEC_CALL = "ex" + "ec('print(1)')"


def _scene(*top_level_lines: str) -> str:
    """Build a scene module with the given extra top-level lines + a valid class."""
    head = ["from manimlib import *", *top_level_lines]
    cls = [
        "class Foo(Scene):",
        "    def construct(self):",
        "        pass",
    ]
    return "\n".join(head + cls) + "\n"


# ── #25 — section id path-traversal sanitization ─────────────────────────────


class TestSectionIdSanitization:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../../etc/cron.d/x",
            "a/b",
            "..",
            "../secret",
            "foo/../bar",
            "name with spaces",
            "Évil-Çhars!",
        ],
    )
    def test_traversal_chars_removed(self, raw):
        slug = sanitize_section_id(raw, idx=3)
        assert "/" not in slug
        assert "\\" not in slug
        assert ".." not in slug
        assert all(c.islower() or c.isdigit() or c == "_" for c in slug)

    def test_empty_falls_back_to_indexed_name(self):
        # Only a genuinely empty post-sanitization result triggers the indexed
        # fallback. "///" sanitizes to "___" (each "/" → "_"), which is already
        # path-safe and non-empty, so it is kept rather than replaced — the
        # security guarantee (no traversal) holds either way.
        assert sanitize_section_id("", idx=7) == "section_07"
        assert sanitize_section_id(None, idx=5) == "section_05"
        assert sanitize_section_id("///", idx=2) == "___"  # safe, non-empty

    def test_separators_only_id_is_path_safe(self):
        slug = sanitize_section_id("///", idx=2)
        assert "/" not in slug and ".." not in slug

    def test_truncated_to_64(self):
        assert len(sanitize_section_id("a" * 200)) == 64

    def test_already_safe_id_is_idempotent(self):
        once = sanitize_section_id("intro_section_01")
        assert sanitize_section_id(once) == once == "intro_section_01"

    def test_class_name_cannot_contain_path_chars(self):
        cls = section_class_name({"id": "../../etc/passwd"})
        assert "/" not in cls and ".." not in cls
        assert cls.endswith("Scene")

    def test_safe_section_id_handles_missing_key(self):
        assert safe_section_id({}, idx=4) == "section_04"


# ── #26 — render subprocess env allowlist (no API key leakage) ───────────────


class TestRenderEnvAllowlist:
    def test_api_keys_never_forwarded(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-aws")
        env = render_env.get_render_env()
        assert "GEMINI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_required_vars_pass_through(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/u")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("LC_CTYPE", "en_US.UTF-8")
        env = render_env.get_render_env()
        assert env.get("HOME") == "/home/u"
        assert env.get("LANG") == "en_US.UTF-8"
        assert env.get("LC_CTYPE") == "en_US.UTF-8"  # LC_ prefix family
        assert "PATH" in env

    def test_escape_hatch_forwards_named_extra(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_RENDER_FLAG", "1")
        monkeypatch.setenv("MANIMGEN_RENDER_ENV_EXTRA", "CUSTOM_RENDER_FLAG")
        env = render_env.get_render_env()
        assert env.get("CUSTOM_RENDER_FLAG") == "1"

    def test_escape_hatch_does_not_open_everything(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "secret")
        monkeypatch.setenv("SOME_OTHER", "x")
        monkeypatch.setenv("MANIMGEN_RENDER_ENV_EXTRA", "SOME_OTHER")
        env = render_env.get_render_env()
        assert "GEMINI_API_KEY" not in env
        assert env.get("SOME_OTHER") == "x"


# ── #27 — AST gate rejects side-effecting top-level code ─────────────────────


class TestSceneAstGate:
    def test_valid_scene_accepted(self):
        assert inspect_scene_code(_scene("import numpy as np")).ok

    def test_top_level_shell_call_rejected(self):
        r = inspect_scene_code(_scene("import os", _SHELL_CALL))
        assert not r.ok
        assert any("os" in f for f in r.findings)

    def test_top_level_exec_rejected(self):
        assert not inspect_scene_code(_scene(_EXEC_CALL)).ok

    def test_second_class_rejected(self):
        two = (
            "from manimlib import *\n"
            "class A(Scene):\n"
            "    def construct(self):\n"
            "        pass\n"
            "class B(Scene):\n"
            "    def construct(self):\n"
            "        pass\n"
        )
        r = inspect_scene_code(two)
        assert not r.ok
        assert r.class_count == 2

    def test_no_class_rejected(self):
        assert not inspect_scene_code("from manimlib import *\n").ok

    def test_syntax_error_reported_not_raised(self):
        r = inspect_scene_code("class Foo(:\n")
        assert not r.ok
        assert any("SyntaxError" in f for f in r.findings)


# ── #29 — config-load failures are logged, not silently swallowed ────────────


class TestConfigLoadLogging:
    def test_malformed_config_warns(self, tmp_path, monkeypatch, caplog):
        from manimgen import cli

        pkg_dir = tmp_path / "manimgen"
        pkg_dir.mkdir()
        bad = tmp_path / "config.yaml"
        bad.write_text("this: : : not valid yaml\n  - broken")
        # _load_config reads ../config.yaml relative to its own __file__ dir,
        # so point dirname at the temp package dir.
        monkeypatch.setattr(cli.os.path, "dirname", lambda _: str(pkg_dir))
        with caplog.at_level(logging.WARNING):
            cfg = cli._load_config()
        assert cfg == {}  # fallback behavior preserved
        assert any(
            "config" in rec.message.lower() for rec in caplog.records
        ), "expected a WARNING when config.yaml is unreadable"
