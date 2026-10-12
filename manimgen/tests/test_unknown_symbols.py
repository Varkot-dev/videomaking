"""Unknown ManimGL names are caught before rendering (#30, #60).

A real Director draft used `Star`, which ManimGL 1.7.2 does not export. The old
report-only shadow check logged it and the render then crashed with NameError.
These tests pin the enforced behaviour: the name becomes a precheck error that
routes into the existing retry path, with real alternatives in the message.

Zero LLM calls, zero subprocess calls, no display needed.
"""

import json
from pathlib import Path

import pytest

from manimgen.generator import scene_generator
from manimgen.validator import manimlib_symbols as ms
from manimgen.validator.codeguard import precheck_and_autofix_file

FIXTURE = Path(__file__).parent / "fixtures" / "director_star_scene.py"


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.delenv(ms.ENFORCE_ENV, raising=False)


def _scene(tmp_path, body: str) -> Path:
    p = tmp_path / "section_01.py"
    p.write_text(
        "from manimlib import *\n\n\nclass S(Scene):\n    def construct(self):\n"
        + "".join(f"        {line}\n" for line in body.splitlines()),
        encoding="utf-8",
    )
    return p


def _names(code: str) -> list[str]:
    return [n for n, _ in ms.find_unknown_names(code)]


class TestRealStarScene:
    def test_star_is_absent_from_shipped_table(self):
        assert "Star" not in ms.load_shipped_symbols()

    def test_precheck_file_rejects_star(self, tmp_path):
        scene = tmp_path / "section_01.py"
        scene.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        result = precheck_and_autofix_file(str(scene))
        assert result["ok"] is False
        assert "Precheck failed" in result["stderr"]
        assert "`Star`" in result["stderr"]
        assert "RegularPolygon" in result["stderr"] or "Polygon" in result["stderr"]

    def test_first_draft_routes_to_scene_precheck_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            scene_generator.paths, "scenes_dir", lambda: str(tmp_path / "scenes")
        )
        monkeypatch.setattr(
            scene_generator,
            "chat",
            lambda **kw: FIXTURE.read_text(encoding="utf-8"),
        )
        monkeypatch.setattr(scene_generator, "load_reference_frames", lambda: None)
        monkeypatch.setattr(scene_generator, "_load_examples_text", lambda s: "")
        section = {"id": "section_01", "title": "t", "narration": "x", "cues": []}
        with pytest.raises(scene_generator.ScenePrecheckError) as ei:
            scene_generator.generate_scenes(section, cue_durations=[5.0])
        assert "Star" in str(ei.value)
        assert Path(ei.value.scene_path).exists()

    def test_kill_switch_reports_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ms.ENFORCE_ENV, "report")
        scene = tmp_path / "section_01.py"
        scene.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        result = precheck_and_autofix_file(str(scene))
        assert result["ok"] is True
        assert "Star" not in result["stderr"]


class TestNoFalsePositives:
    def test_names_defined_in_file(self):
        code = (
            "from manimlib import *\n"
            "def helper(a, *rest, k=1, **kw):\n    return a\n"
            "class Thing: pass\n"
            "CONST = 3\n"
            "x = helper(CONST) + Thing()\n"
        )
        assert _names(code) == []

    def test_defined_after_use_in_same_file(self):
        code = (
            "from manimlib import *\ndef f():\n    return Later()\nclass Later: pass\n"
        )
        assert _names(code) == []

    def test_comprehension_loop_params_lambda_with_except_global(self):
        code = (
            "from manimlib import *\n"
            "ys = [q * 2 for q in range(3)]\n"
            "g = {k: v for k, v in zip('ab', ys)}\n"
            "f = lambda m: m + 1\n"
            "for i, (a, b) in enumerate([(1, 2)]):\n    print(i, a, b)\n"
            "with open('x') as fh:\n    print(fh)\n"
            "try:\n    pass\nexcept ValueError as err:\n    print(err)\n"
            "def setter():\n    global late\n    late = 1\n"
            "print(late, (w := 3), w)\n"
        )
        assert _names(code) == []

    def test_imports_and_aliases(self):
        code = "import itertools as itx\nfrom math import sqrt\nprint(itx, sqrt(2))\n"
        assert _names(code) == []

    def test_builtins_and_dunders(self):
        assert (
            _names("print(len(range(3)), __name__, sum([1]), isinstance(1, int))\n")
            == []
        )

    def test_strings_comments_attributes_keywords(self):
        code = (
            "from manimlib import *\n"
            "# Star is not defined in ManimGL\n"
            "s = 'Star(1)'\n"
            "d = '''Star'''\n"
            "obj = Circle()\n"
            "obj.Star\n"
            "obj.set_color(Star=1)\n"
            "f'{s} Star'\n"
        )
        assert _names(code) == []

    def test_real_manimlib_names(self):
        code = "from manimlib import *\nx = [Circle, Square, ShowCreation, PI, UP, np, TEAL_A]\n"
        assert _names(code) == []

    def test_foreign_star_import_is_not_judged(self):
        assert _names("from somewhere import *\nprint(Mystery)\n") == []

    def test_syntax_error_is_not_ours(self):
        assert _names("def (:\n") == []

    def test_no_table_fails_open(self, monkeypatch):
        monkeypatch.setattr(ms, "load_manimlib_symbols", lambda: None)
        assert _names("print(Star)\n") == []


class TestDetection:
    def test_bare_name_not_called(self):
        # Not only call targets: a bare Name load (e.g. as an argument) counts.
        assert _names("x = [Bogus, 1]\n") == ["Bogus"]

    def test_reports_first_line_and_dedupes(self):
        found = ms.find_unknown_names("a = 1\nb = Bogus\nc = Bogus\n")
        assert found == [("Bogus", 2)]

    def test_suggestions_are_real_names(self):
        alts = ms.suggest_alternatives("MathTex")
        assert "Tex" in alts
        assert set(alts) <= ms.load_manimlib_symbols()

    def test_message_names_symbol_and_alternatives(self):
        (msg,) = ms.format_unknown_symbol_errors([("Star", 3)])
        assert "`Star`" in msg and "line 3" in msg and "Polygon" in msg


class TestShippedTable:
    def test_json_shape(self):
        data = json.loads(ms.SYMBOLS_JSON.read_text(encoding="utf-8"))
        assert data["manimgl_version"] == "1.7.2"
        assert data["names"] == sorted(set(data["names"]))
        assert {"Scene", "Circle", "ShowCreation", "np"} <= set(data["names"])

    def test_shipped_table_equals_live_manimlib(self):
        live = ms.live_star_import_names()
        if live is None:
            pytest.skip("manimlib not importable here (needs a display)")
        assert ms.load_shipped_symbols() == live, (
            "manimgl changed: rerun scripts/gen_manimlib_symbols.py"
        )

    def test_fallback_used_when_live_import_fails(self, monkeypatch):
        monkeypatch.setattr(ms, "live_star_import_names", lambda: None)
        ms.load_manimlib_symbols.cache_clear()
        try:
            assert ms.load_manimlib_symbols() == ms.load_shipped_symbols()
        finally:
            ms.load_manimlib_symbols.cache_clear()
