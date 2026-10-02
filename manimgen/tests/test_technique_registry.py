"""One technique registry: planner, critic, 3D promotion and example tags agree."""

import re
from pathlib import Path

import pytest

from manimgen import techniques
from manimgen.generator import scene_generator as sg
from manimgen.planner import lesson_planner as lp

PKG = Path(__file__).resolve().parent.parent / "manimgen"
EXAMPLES = (
    (PKG / "examples") if (PKG / "examples").is_dir() else PKG.parent / "examples"
)
_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|", re.MULTILINE)
_TAG = re.compile(r"techniques:\s*(.+)", re.IGNORECASE)


def _rows(path: Path) -> set[str]:
    return set(_ROW.findall(path.read_text(encoding="utf-8")))


def test_registry_has_unique_names_and_descriptions():
    names = [t.name for t in techniques.TECHNIQUES]
    assert len(names) == len(set(names))
    assert all(t.description for t in techniques.TECHNIQUES)


def test_planner_menu_is_the_registry():
    assert lp._technique_menu() == techniques.TECHNIQUE_NAMES


def test_critic_prompt_lists_every_registry_technique():
    prompt = lp._load_critic_system_prompt()
    for name in techniques.TECHNIQUE_NAMES:
        assert f"`{name}`" in prompt


def test_planner_prompt_table_matches_registry():
    rows = _rows(PKG / "planner" / "prompts" / "planner_system.md")
    assert rows == techniques.TECHNIQUE_NAMES


def test_director_prompt_table_matches_registry():
    text = (PKG / "generator" / "prompts" / "director_system.md").read_text(
        encoding="utf-8"
    )
    assert techniques.TECHNIQUE_NAMES <= set(_ROW.findall(text))


def test_director_prompt_marks_exactly_the_3d_techniques():
    text = (PKG / "generator" / "prompts" / "director_system.md").read_text(
        encoding="utf-8"
    )
    marked = {
        m.group(1)
        for m in _ROW.finditer(text)
        if "requires ThreeDScene" in text[m.start() : text.index("\n", m.start())]
    }
    assert marked == techniques.THREE_D_TECHNIQUES


@pytest.mark.parametrize("name", sorted(techniques.THREE_D_TECHNIQUES))
def test_each_3d_technique_requests_3d(name):
    assert sg._requests_3d(f"technique: {name} on a surface")


@pytest.mark.parametrize("name", sorted(techniques.THREE_D_TECHNIQUES))
@pytest.mark.parametrize("neg", ["no", "not", "without", "avoid", "skip"])
def test_negated_3d_technique_is_not_requested(name, neg):
    assert not sg._requests_3d(f"keep it flat, {neg} {name}")


def test_2d_techniques_never_promote():
    for t in techniques.TECHNIQUES:
        if not t.is_3d:
            assert not sg._requests_3d(f"technique: {t.name}")


def test_the_issue_repro_three_missing_techniques_promote():
    assert sg._requests_3d("technique: camera_flythrough dot_product_3d")
    assert sg._requests_3d("technique: cross_section_3d")


def test_technique_added_to_registry_reaches_every_consumer(monkeypatch):
    extra = techniques.Technique("zz_new_3d", True, "a test technique")
    new = techniques.TECHNIQUES + (extra,)
    monkeypatch.setattr(techniques, "TECHNIQUE_NAMES", frozenset(t.name for t in new))
    monkeypatch.setattr(
        techniques,
        "THREE_D_TECHNIQUES",
        frozenset(t.name for t in new if t.is_3d),
    )
    assert "zz_new_3d" in lp._technique_menu()
    assert "`zz_new_3d`" in lp._load_critic_system_prompt()
    assert sg._requests_3d("technique: zz_new_3d")


def test_every_example_tag_is_a_registry_technique():
    for path in sorted(EXAMPLES.glob("*.py")):
        head = path.read_text(encoding="utf-8")[:512]
        m = _TAG.search(head)
        assert m, f"{path.name} has no techniques: tag"
        for tag in (t.strip() for t in m.group(1).split(",")):
            assert tag in techniques.TECHNIQUE_NAMES, f"{path.name}: {tag}"
