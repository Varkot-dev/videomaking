"""Tests for the manimlib allowlist shadow-mode check (#30).

codeguard is historically an unbounded denylist. #30 adds the inverse — an
allowlist membership check against the pinned manimlib symbol table — but lands
it in REPORT-ONLY / shadow mode: it logs what it *would* flag and must NEVER
block, degrade, or alter a render this cycle. These tests pin that contract:

  1. shadow_check_allowlist flags an unknown call target,
  2. it never flags builtins / locally bound names / real manimlib symbols,
  3. it fails open (returns []) when the symbol table is unavailable,
  4. precheck_and_autofix(_file) keeps shipping clean output even when a name
     would be flagged — shadow mode does not block.

Zero LLM calls, zero subprocess calls.
"""

import logging

from manimgen.validator import manimlib_symbols
from manimgen.validator.codeguard import (
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


class TestShadowModeIsNonBlocking:
    """The core #30 guarantee: shadow logging NEVER blocks or degrades output."""

    def test_shadow_log_returns_flagged_but_does_not_raise(self, monkeypatch):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        flagged = _shadow_log_unknown_symbols(_SCENE_WITH_UNKNOWN)
        assert "TotallyMadeUpMobject" in flagged

    def test_shadow_log_emits_info_only(self, monkeypatch, caplog):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        with caplog.at_level(logging.INFO):
            _shadow_log_unknown_symbols(_SCENE_WITH_UNKNOWN)
        # It logs at INFO (report-only) and never escalates to WARNING+.
        assert any("allowlist-shadow" in r.message for r in caplog.records)
        assert all(r.levelno < logging.WARNING for r in caplog.records)

    def test_precheck_does_not_block_on_unknown_symbol(self, monkeypatch):
        # Even with a name the allowlist WOULD flag, precheck must not introduce
        # an error from the allowlist — shadow mode is report-only. The returned
        # code is unchanged by the allowlist (autofixes aside).
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        out = precheck_and_autofix(_SCENE_WITH_UNKNOWN)
        # The unknown symbol is left intact: shadow mode never rewrites/strips it.
        assert "TotallyMadeUpMobject" in out

    def test_precheck_file_ok_despite_flagged_symbol(self, monkeypatch, tmp_path):
        _fake_symbols(monkeypatch, {"Scene", "ShowCreation"})
        scene = tmp_path / "section_01.py"
        scene.write_text(_SCENE_WITH_UNKNOWN)
        result = precheck_and_autofix_file(str(scene))
        # No denylist/validate error here, so precheck stays ok — the allowlist
        # shadow check did NOT contribute a blocking error or layout warning.
        assert result["ok"] is True
        flagged_in_warnings = any(
            "TotallyMadeUpMobject" in w for w in result.get("layout_warnings", [])
        )
        assert not flagged_in_warnings
