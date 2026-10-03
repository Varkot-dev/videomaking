"""Few-shot example selection covers every requested technique within budget."""

import logging
from pathlib import Path

import pytest

from manimgen import techniques
from manimgen.generator import scene_generator as sg

ROOT = Path(__file__).resolve().parent.parent


def _section(*visuals: str) -> dict:
    return {
        "id": "s1",
        "cues": [{"index": i, "visual": v} for i, v in enumerate(visuals)],
    }


def _select(section: dict) -> list[str]:
    return sg._select_examples(section, sg._index_examples())


def _tags(path: str) -> set[str]:
    return {t for t, paths in sg._index_examples().items() if path in paths}


def test_issue_repro_late_techniques_get_examples():
    section = _section(
        "Technique: stagger_reveal",
        "Technique: sweep_highlight",
        "Technique: camera_zoom",
        "Technique: equation_morph",
    )
    chosen = _select(section)
    assert len(chosen) <= sg._MAX_EXAMPLES
    covered = set().union(*(_tags(p) for p in chosen))
    assert {"stagger_reveal", "sweep_highlight", "camera_zoom", "equation_morph"} <= (
        covered
    )


def _exampled() -> list[str]:
    index = sg._index_examples()
    return sorted(t for t in techniques.TECHNIQUE_NAMES if t in index)


def test_most_registry_techniques_have_an_example():
    assert len(_exampled()) >= 18


@pytest.mark.parametrize("name", _exampled())
def test_each_technique_late_in_a_four_cue_section_gets_its_example(name):
    filler = ["Technique: tracker_label", "Technique: fade_reveal"]
    filler = [f for f in filler if name not in f]
    section = _section(*filler, "Technique: brace_annotation", f"Technique: {name}")
    chosen = _select(section)
    assert len(chosen) <= sg._MAX_EXAMPLES
    assert any(name in _tags(p) for p in chosen), (name, chosen)


def test_baselines_fill_only_remaining_slots():
    chosen = [Path(p).name for p in _select(_section("Technique: camera_zoom"))]
    assert "camera_zoom_scene.py" in chosen
    assert chosen[0] != "graph_scene.py"
    assert "graph_scene.py" in chosen
    assert len(chosen) <= sg._MAX_EXAMPLES


def test_no_techniques_falls_back_to_baselines():
    chosen = [Path(p).name for p in _select(_section("some plain words"))]
    assert chosen == ["graph_scene.py", "stagger_build_scene.py"]


def test_more_requested_than_slots_is_capped_and_unique():
    names = sorted(_exampled())
    chosen = _select(_section(*(f"Technique: {n}" for n in names)))
    assert len(chosen) == len(set(chosen)) <= sg._MAX_EXAMPLES


def test_one_example_covering_two_techniques_uses_one_slot():
    chosen = _select(_section("3d_surface", "camera_rotation"))
    assert len(chosen) == 1 or all(
        {"3d_surface", "camera_rotation"} & _tags(p) for p in chosen[:1]
    )
    assert Path(chosen[0]).name == "parametric_surface_scene.py"


def test_missing_examples_folder_warns_and_yields_nothing(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(sg, "_examples_dir", lambda: tmp_path / "nope")
    with caplog.at_level(logging.WARNING, logger=sg.logger.name):
        assert sg._index_examples() == {}
        assert sg._load_examples_text(_section("Technique: camera_zoom")) == ""
    assert "examples folder not found" in caplog.text


def test_examples_ship_as_package_data():
    setup_py = (ROOT / "setup.py").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "examples/*.py" in setup_py
    assert "manimgen/examples" in manifest
    assert (ROOT / "manimgen" / "examples" / "graph_scene.py").is_file()
    assert not (ROOT / "examples").exists()
