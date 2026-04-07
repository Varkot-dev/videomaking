"""
Tests for manimgen/generator/scene_generator.py

Tests narration duration estimation and class name generation.
LLM calls are mocked — zero API cost.
"""

import pytest
from manimgen.generator.scene_generator import (
    _estimate_duration,
    _class_name,
    _WORDS_PER_MINUTE,
)

# Aliases for test readability
_estimate_narration_duration = _estimate_duration
_class_name_from_section = _class_name


# ── _estimate_duration ──────────────────────────────────────────────────────

class TestEstimateNarrationDuration:

    def test_empty_string_returns_minimum(self):
        assert _estimate_narration_duration("") == 10

    def test_single_word_returns_minimum(self):
        assert _estimate_narration_duration("hello") == 10

    def test_known_word_count(self):
        # 130 words at 130 wpm = exactly 60 seconds
        text = " ".join(["word"] * 130)
        assert _estimate_narration_duration(text) == 60

    def test_half_minute(self):
        # 65 words = 30 seconds
        text = " ".join(["word"] * 65)
        assert _estimate_narration_duration(text) == 30

    def test_rounds_up(self):
        # 131 words at 130 wpm = 60.46s → ceil → 61
        text = " ".join(["word"] * 131)
        assert _estimate_narration_duration(text) == 61

    def test_long_narration(self):
        # 390 words = 3 minutes = 180 seconds
        text = " ".join(["word"] * 390)
        assert _estimate_narration_duration(text) == 180

    def test_minimum_enforced_for_short_text(self):
        # 5 words well under minimum
        assert _estimate_narration_duration("a b c d e") == 10

    def test_words_per_minute_constant(self):
        assert _WORDS_PER_MINUTE == 130

    def test_typical_paragraph_in_range(self):
        # A typical 4-6 sentence narration should be 20-50 seconds
        text = (
            "Binary search is a powerful algorithm that finds items in sorted lists. "
            "Instead of checking every element, it repeatedly halves the search space. "
            "Starting from the middle, it compares the target to the midpoint. "
            "If the target is smaller, the right half is eliminated. "
            "This continues until the element is found or the list is exhausted."
        )
        duration = _estimate_narration_duration(text)
        assert 20 <= duration <= 60


# ── _class_name ──────────────────────────────────────────────────────────────

class TestClassNameFromSection:

    def test_basic_section_id(self):
        section = {"id": "section_01"}
        assert _class_name_from_section(section) == "Section01Scene"

    def test_multi_word_id(self):
        section = {"id": "section_binary_search"}
        assert _class_name_from_section(section) == "SectionBinarySearchScene"

    def test_single_word_id(self):
        section = {"id": "intro"}
        assert _class_name_from_section(section) == "IntroScene"

    def test_always_ends_with_scene(self):
        for id_ in ["section_01", "conclusion", "part_two_recap"]:
            name = _class_name_from_section({"id": id_})
            assert name.endswith("Scene")

    def test_no_underscores_in_output(self):
        section = {"id": "section_03_deep_dive"}
        name = _class_name_from_section(section)
        assert "_" not in name

    def test_is_valid_python_identifier(self):
        section = {"id": "section_01"}
        name = _class_name_from_section(section)
        assert name.isidentifier()
