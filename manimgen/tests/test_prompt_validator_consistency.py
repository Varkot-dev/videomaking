"""Cross-check the LLM prompts against the validators they are enforced by.

The Director and planner prompts once disagreed with the validators (title
buff 0.35 vs 0.8, off-scale font sizes, a banned ``scale_factor`` kwarg taught
as an example, decorative YELLOW, two titles at ``to_edge(UP)``, a claim that
reference screenshots were attached). Each disagreement ends as a validator
warning fed to the retry call or a silent rewrite that changes the look.

Where a prompt and a validator disagree, the validator wins: these tests parse
the stable, machine-checkable statements out of the prompt text and compare
them with the validator constants. When one fails, fix the prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from manimgen.utils import load_reference_frames
from manimgen.validator import codeguard, invariants

PKG = Path(__file__).resolve().parent.parent / "manimgen"
DIRECTOR = PKG / "generator" / "prompts" / "director_system.md"
PLANNER_FILES = sorted((PKG / "planner" / "prompts").glob("planner*_system.md"))
RETRY = PKG / "validator" / "prompts" / "retry_system.md"
ALL_PROMPTS = [DIRECTOR, RETRY, *PLANNER_FILES]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ids(paths):
    return [p.name for p in paths]


def _validator_source(name: str) -> str:
    return (PKG / "validator" / name).read_text(encoding="utf-8")


def test_prompt_files_found():
    assert DIRECTOR.exists() and RETRY.exists()
    assert len(PLANNER_FILES) >= 2


def test_canonical_font_sizes_agree_between_validators():
    assert set(codeguard._CANONICAL_FONT_SIZES) == set(invariants._CANONICAL_FONT_SIZES)


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=_ids(ALL_PROMPTS))
def test_prompt_font_sizes_are_canonical(path):
    """font_size=N in code and `font_size N` in planner prose must be on the type scale."""
    canonical = set(invariants._CANONICAL_FONT_SIZES)
    # The anti-pattern table row that shows an invented size as the BAD example is exempt.
    text = "\n".join(ln for ln in _text(path).splitlines() if "Invented font size" not in ln)
    sizes = {int(n) for n in re.findall(r"\bfont_size(?:\s*=\s*|\s+)(\d+)", text)}
    off = sorted(sizes - canonical)
    assert not off, f"{path.name}: off-scale font sizes {off}; canonical is {sorted(canonical)}"


def test_director_font_size_range_in_prose_is_canonical():
    """Ranges like `font_size=36–44` name two sizes; both must be canonical."""
    canonical = set(invariants._CANONICAL_FONT_SIZES)
    for lo, hi in re.findall(r"font_size=(\d+)\s*[–-]\s*(\d+)", _text(DIRECTOR)):
        assert int(lo) in canonical and int(hi) in canonical


def _title_buff_from_validator() -> str:
    found = set(re.findall(r"to_edge\(UP, buff=(\d+(?:\.\d+)?)\)", _validator_source("invariants.py")))
    assert len(found) == 1, f"invariants.py should state one title buff, found {found}"
    return found.pop()


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=_ids(ALL_PROMPTS))
def test_prompt_title_buff_matches_validator(path):
    want = float(_title_buff_from_validator())
    for m in re.finditer(r"to_edge\(UP,\s*buff=(\d+(?:\.\d+)?)\)", _text(path)):
        assert float(m.group(1)) == want, f"{path.name}: to_edge(UP, buff={m.group(1)}) but validator says {want}"


@pytest.mark.parametrize("path", [DIRECTOR, RETRY], ids=["director", "retry"])
def test_prompt_code_titles_always_pass_the_buff(path):
    """Inside code fences a title is `.to_edge(UP, buff=...)`, never a bare `.to_edge(UP)`."""
    in_fence = False
    for line in _text(path).splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and re.search(r"\.to_edge\(UP\)", line):
            raise AssertionError(f"{path.name}: bare .to_edge(UP) in code: {line.strip()}")


def test_title_zone_boundary_matches_invariants():
    zone = re.search(r"y∈\[(\d+(?:\.\d+)?),", _validator_source("invariants.py"))
    assert zone, "invariants.py no longer states the title zone"
    low = zone.group(1)
    text = _text(DIRECTOR)
    assert re.search(rf"TITLE:\s+y ∈ \[{re.escape(low)},", text)
    stated = set(re.findall(r"y > (\d+(?:\.\d+)?)", text))
    assert stated == {low}, f"director prompt title-zone boundaries {stated}, validator {low}"


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=_ids(ALL_PROMPTS))
def test_prompt_teaches_no_banned_kwarg(path):
    # A line that states the prohibition ("banned", "no tip_length=") is not a teaching example.
    prohibition = re.compile(r"\b(ban|banned|no|never|wrong|not)\b", re.I)
    text = "\n".join(ln for ln in _text(path).splitlines() if not prohibition.search(ln))
    for kw in codeguard._BANNED_KWARGS:
        assert not re.search(rf"\b{kw}\s*=", text), f"{path.name} teaches banned kwarg {kw}="


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=_ids(ALL_PROMPTS))
def test_prompt_hex_literals_are_sanctioned_or_mapped(path):
    mapped = set(re.findall(r'"(#[0-9A-Fa-f]{6})"', _validator_source("codeguard.py")))
    allowed = {h.lower() for h in invariants._SANCTIONED_HEXES} | {h.lower() for h in mapped}
    used = {h.lower() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", _text(path))}
    assert used <= allowed, f"{path.name}: hex colors the validators would flag or rewrite: {sorted(used - allowed)}"


def test_director_palette_roles_match_codeguard():
    block = re.search(r"# Palette roles.*?```", _text(DIRECTOR), re.S).group(0)
    roles = dict(re.findall(r"^([A-Z]+)\s*=\s*([A-Z_]+)\s", block, re.M))
    assert roles == codeguard._COLOR_ROLE_CONSTANTS


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=_ids(ALL_PROMPTS))
def test_yellow_is_never_decorative(path):
    """YELLOW is the WARNING role (invariants I3); examples must not use it as decoration."""
    text = _text(path)
    assert "YELLOW" in invariants._ROLE_CONSTANTS
    assert not re.search(r"color\s*=\s*YELLOW\b", text)
    if path in PLANNER_FILES:
        assert not re.search(r"\byellow\b", text, re.I)


def test_archetype_c_has_one_title_zone_mobject():
    text = _text(DIRECTOR)
    section = text[text.index("### Archetype C") : text.index("### Title rule")]
    assert "two titles at" not in section
    assert "shift(LEFT*3.2)" not in section.replace(" ", "")


def test_planner_few_shots_do_not_clear_the_screen_mid_scene():
    """The Director forbids a black screen and a mid-scene full FadeOut."""
    pattern = re.compile(r"screen clears|\ball\b[^.\"]*\bfade out\b|fade out all", re.I)
    for path in PLANNER_FILES:
        for line in _text(path).splitlines():
            if '"visual"' in line:
                assert not pattern.search(line), f"{path.name}: {line.strip()[:120]}"


def test_planner_visuals_avoid_title_zone_corners():
    """to_corner placements in the title zone are forbidden by the Director and I2."""
    for path in PLANNER_FILES:
        for line in _text(path).splitlines():
            if '"visual"' in line:
                assert not re.search(r"top-(?:right|left)", line, re.I), path.name


def test_director_does_not_claim_images_that_are_not_shipped():
    text = _text(DIRECTOR)
    if not load_reference_frames():
        assert not re.search(r"screenshot|reference frames|provided with actual", text, re.I)
    assert "bottom of this prompt" not in text


def test_director_does_not_teach_become_then_showcreation():
    """Codeguard rewrites self.play(x.become(..)) to .animate.become(..); the prompt must
    teach that form, not 'call become() first, then ShowCreation' (snaps, then re-draws)."""
    text = _text(DIRECTOR)
    assert not re.search(r"become\([^\n]*BEFORE self\.play", text)
    assert "call become() first" not in text
    # No code line may be a bare become() statement followed by a ShowCreation of the same name.
    assert not re.search(
        r"^\s*(\w+)\.become\([^\n]*\)\s*\n\s*self\.play\(ShowCreation\(\1\)", text, re.M
    )
    # The taught form is the one codeguard produces.
    assert "self.play(scan_rect.animate.become(" in text
    fixed, applied = codeguard._fix_become_inside_play("self.play(r.become(Square()))\n")
    assert ".animate.become(" in fixed and applied


def test_director_scale_factor_guidance_matches_validator():
    """Indicate takes scale_factor, FadeIn/FadeOut do not; the prompt must not teach the latter."""
    text = _text(DIRECTOR)
    assert not re.search(r"Fade(?:In|Out)\([^)\n]*scale_factor", text)
    assert codeguard._BANNED_KWARGS["scale_factor"] == frozenset({"FadeIn", "FadeOut"})
