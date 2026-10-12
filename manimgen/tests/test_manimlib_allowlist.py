"""Tests for the manimlib allowlist shadow-mode check (#30).

codeguard is historically an unbounded denylist. #30 adds the inverse — an
allowlist membership check against the pinned manimlib symbol table — but lands
it in REPORT-ONLY / shadow mode: it logs what it *would* flag and must NEVER
block, degrade, or alter a render this cycle. These tests pin that contract:

  1. shadow_check_allowlist flags an unknown call target,
  2. it never flags builtins / locally bound names / real manimlib symbols,
  3. it fails open (returns []) when the symbol table is unavailable,
  4. unknown names now BLOCK precheck by default (see test_unknown_symbols.py);
     MANIMGEN_UNKNOWN_SYMBOLS=report restores the original report-only mode.

Zero LLM calls, zero subprocess calls.
"""

import logging

from manimgen.validator import manimlib_symbols
from manimgen.validator.codeguard import (
    _check_unknown_symbols,
    _shadow_log_unknown_symbols,
    precheck_and_autofix,
    precheck_and_autofix_file,
)

# A scene that uses one obviously-hallucinated, non-manimlib call target.
_SCENE_WITH_UNKNOWN = """\
from manimlib import *


class Demo(Scene):
    def construct(self):
        thing = TotallyMadeUpMobject(radius=1)
        self.play(ShowCreation(thing))
        self.wait(1.0)
"""


def _fake_symbols(monkeypatch, names):
    """Force the allowlist symbol table to a known set (bypasses real manimlib)."""
    monkeypatch.setattr(
        manimlib_symbols,
        "load_manimlib_symbols",
        lambda: frozenset(names),
    )


class TestShadowCheckAllowlist:
    def test_flags_unknown_call_target(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        flagged = manimlib_symbols.shadow_check_allowlist(_SCENE_WITH_UNKNOWN)
        assert "TotallyMadeUpMobject" in flagged

    def test_known_manimlib_symbol_not_flagged(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation", "Circle"})
        code = "from manimlib import *\nx = Circle()\n"
        assert manimlib_symbols.shadow_check_allowlist(code) == []

    def test_builtins_not_flagged(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene"})
        code = "vals = list(range(3))\nn = len(vals)\n"
        assert manimlib_symbols.shadow_check_allowlist(code) == []

    def test_locally_bound_name_not_flagged(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene"})
        code = (
            "def make_axes():\n"
            "    return 1\n"
            "\n"
            "ax = make_axes()\n"  # locally defined → not an unknown manimlib symbol
        )
        assert manimlib_symbols.shadow_check_allowlist(code) == []

    def test_fails_open_when_symbols_unavailable(self, monkeypatch):
        # manimlib not importable (CI) → load_manimlib_symbols() returns None.
        monkeypatch.setattr(manimlib_symbols, "load_manimlib_symbols", lambda: None)
        assert manimlib_symbols.shadow_check_allowlist(_SCENE_WITH_UNKNOWN) == []

    def test_syntax_error_fails_open(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene"})
        assert manimlib_symbols.shadow_check_allowlist("def (:\n") == []


class TestKillSwitchKeepsReportOnlyMode:
    """MANIMGEN_UNKNOWN_SYMBOLS=report restores the old #30 shadow behaviour."""

    def test_shadow_log_returns_flagged_but_does_not_raise(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        flagged = _shadow_log_unknown_symbols(_SCENE_WITH_UNKNOWN)
        assert "TotallyMadeUpMobject" in flagged

    def test_report_mode_logs_info_only(self, monkeypatch, caplog):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        monkeypatch.setenv(manimlib_symbols.ENFORCE_ENV, "report")
        with caplog.at_level(logging.INFO):
            assert _check_unknown_symbols(_SCENE_WITH_UNKNOWN) == []
        assert any("unknown-symbols" in r.message for r in caplog.records)
        assert all(r.levelno < logging.WARNING for r in caplog.records)

    def test_report_mode_precheck_file_stays_ok(self, monkeypatch, tmp_path):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        monkeypatch.setenv(manimlib_symbols.ENFORCE_ENV, "report")
        scene = tmp_path / "section_01.py"
        scene.write_text(_SCENE_WITH_UNKNOWN, encoding="utf-8")
        assert precheck_and_autofix_file(str(scene))["ok"] is True

    def test_string_precheck_never_rewrites_unknown_symbol(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        assert "TotallyMadeUpMobject" in precheck_and_autofix(_SCENE_WITH_UNKNOWN)


class TestEnforcedByDefault:
    def test_precheck_file_blocks_unknown_symbol(self, monkeypatch, tmp_path):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        monkeypatch.delenv(manimlib_symbols.ENFORCE_ENV, raising=False)
        scene = tmp_path / "section_01.py"
        scene.write_text(_SCENE_WITH_UNKNOWN, encoding="utf-8")
        result = precheck_and_autofix_file(str(scene))
        assert result["ok"] is False
        assert "TotallyMadeUpMobject" in result["stderr"]
