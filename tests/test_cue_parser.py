"""
Tests for manimgen/planner/cue_parser.py

parse_cues() and inject_cues() — no LLM calls, pure string processing.
"""

import pytest
from manimgen.planner.cue_parser import parse_cues, inject_cues


# ---------------------------------------------------------------------------
# parse_cues
# ---------------------------------------------------------------------------

class TestParseCues:

    def test_single_cue_produces_two_segments(self):
        text = "Half the list. [CUE] Now check the middle."
        clean, indices = parse_cues(text)
        assert clean == "Half the list. Now check the middle."
        # word 0 = "Half", word 3 = "Now"
        assert indices == [0, 3]

    def test_no_cues_returns_only_index_zero(self):
        text = "Binary search is fast and elegant."
        clean, indices = parse_cues(text)
        assert clean == text
        assert indices == [0]

    def test_multiple_cues(self):
        text = "First part here. [CUE] Second part now. [CUE] Third part last."
        clean, indices = parse_cues(text)
        assert clean == "First part here. Second part now. Third part last."
        # "First part here." = 3 words → cue at 3
        # "Second part now." = 3 words → cue at 6
        assert indices == [0, 3, 6]

    def test_clean_text_has_no_cue_tags(self):
        text = "Start. [CUE] Middle. [CUE] End."
        clean, _ = parse_cues(text)
        assert "[CUE]" not in clean
        assert "[cue]" not in clean.lower()

    def test_whitespace_normalised_around_cue(self):
        # Extra space left by removed tag should be collapsed
        text = "Word one two. [CUE] Word three four."
        clean, _ = parse_cues(text)
        assert "  " not in clean

    def test_case_insensitive_cue_tag(self):
        text = "Hello world. [cue] Goodbye world."
        clean, indices = parse_cues(text)
        assert "[cue]" not in clean.lower()
        assert indices == [0, 2]

    def test_index_zero_always_first(self):
        _, indices = parse_cues("anything [CUE] here")
        assert indices[0] == 0

    def test_indices_strictly_increasing(self):
        text = "a b c d e. [CUE] f g h i j. [CUE] k l m n o."
        _, indices = parse_cues(text)
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)

    def test_cue_at_sentence_boundary(self):
        # Realistic narration — cue between sentences
        text = (
            "Binary search cuts the problem in half every time. "
            "[CUE] Watch the array. The middle element is either your target, "
            "[CUE] or it tells you which half to throw away."
        )
        clean, indices = parse_cues(text)
        assert "[CUE]" not in clean
        assert indices[0] == 0
        assert len(indices) == 3
        # First cue after "Binary search cuts the problem in half every time." (9 tokens via split())
        assert indices[1] == 9

    def test_empty_string(self):
        clean, indices = parse_cues("")
        assert clean == ""
        assert indices == [0]

    def test_only_cue_tag(self):
        # Pathological — just a bare tag
        clean, indices = parse_cues("[CUE]")
        assert clean.strip() == ""
        assert indices == [0]

    def test_returns_tuple_of_str_and_list(self):
        result = parse_cues("Hello. [CUE] World.")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)

    def test_word_count_accuracy(self):
        # 5 words, then [CUE], then 4 words
        text = "one two three four five [CUE] six seven eight nine"
        clean, indices = parse_cues(text)
        assert indices == [0, 5]
        assert clean.split() == ["one","two","three","four","five","six","seven","eight","nine"]


# ---------------------------------------------------------------------------
# inject_cues (inverse)
# ---------------------------------------------------------------------------

class TestInjectCues:

    def test_roundtrip(self):
        original = "Half the list. [CUE] Now check the middle."
        clean, indices = parse_cues(original)
        reconstructed = inject_cues(clean, indices)
        # Re-parse the reconstructed string — should give the same indices
        _, indices2 = parse_cues(reconstructed)
        assert indices == indices2

    def test_index_zero_not_inserted(self):
        clean = "Hello world today"
        result = inject_cues(clean, [0, 2])
        assert not result.startswith("[CUE]")

    def test_cue_inserted_at_correct_word(self):
        clean = "one two three four five"
        result = inject_cues(clean, [0, 3])
        words = result.split()
        # [CUE] should appear before "four" (index 3)
        cue_pos = words.index("[CUE]")
        assert words[cue_pos + 1] == "four"

    def test_multiple_cues_inserted(self):
        clean = "a b c d e f g h i j"
        result = inject_cues(clean, [0, 3, 7])
        assert result.count("[CUE]") == 2

    def test_no_extra_cues_for_index_zero_only(self):
        clean = "one two three"
        result = inject_cues(clean, [0])
        assert "[CUE]" not in result
