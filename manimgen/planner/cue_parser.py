# Cue parser — extracts [CUE] markers from LLM-authored narration.
#
# The LLM writes narration with inline [CUE] tags to mark where a new
# animation should begin. Example:
#
#   "Binary search cuts the problem in half every time. [CUE] Watch the
#    array. The middle element is either your target, [CUE] or it tells
#    you which half to throw away entirely."
#
# parse_cues() strips the tags and returns:
#   - clean narration text (for TTS)
#   - cue_word_indices: list of 0-based word indices at which each [CUE]
#     appeared. Index 0 is always prepended automatically — the first
#     animation starts at word 0.
#
# Word index semantics:
#   cue_word_indices = [0, 9, 17]
#   → animation 0 plays from word 0 until word 9 starts being spoken
#   → animation 1 plays from word 9 until word 17 starts being spoken
#   → animation 2 plays from word 17 until the end of audio
#
# These indices are stored in the lesson plan JSON and later resolved to
# real timestamps via tts.cue_times(word_timestamps, cue_word_indices).

import re

_CUE_TAG = re.compile(r"\[CUE\]", re.IGNORECASE)


def parse_cues(narration_with_cues: str) -> tuple[str, list[int]]:
    """Strip [CUE] markers from narration and return (clean_text, cue_word_indices).

    The returned cue_word_indices always starts with 0 — the first animation
    begins at the very first word. Each subsequent index is the word count
    at the point where that [CUE] tag appeared.

    Example:
        input:  "Half the list. [CUE] Now check the middle."
        output: ("Half the list. Now check the middle.", [0, 3])
                 ^ word 0 = "Half", word 3 = "Now"
    """
    cue_indices: list[int] = [0]
    word_count = 0
    clean_parts: list[str] = []

    # Split on [CUE] tags, keeping track of words seen before each marker
    segments = _CUE_TAG.split(narration_with_cues)

    for i, segment in enumerate(segments):
        words_in_segment = segment.split()
        if i > 0:
            # This segment starts right after a [CUE] — record the word index.
            # Only add if it's different from the last index (guards against
            # bare [CUE] at start/end with no words before/after it).
            if word_count != cue_indices[-1]:
                cue_indices.append(word_count)
        word_count += len(words_in_segment)
        clean_parts.append(segment)

    clean_text = "".join(clean_parts)
    # Normalise whitespace left by removed tags (e.g. double spaces)
    clean_text = re.sub(r"  +", " ", clean_text).strip()

    return clean_text, cue_indices


def inject_cues(clean_text: str, cue_word_indices: list[int]) -> str:
    """Inverse of parse_cues — rebuild narration with [CUE] tags inserted.

    Useful for displaying the authored script in the editor UI.
    Index 0 is always the implicit start and is NOT re-inserted as a tag.
    """
    words = clean_text.split()
    cue_set = set(cue_word_indices) - {0}
    result: list[str] = []
    for i, word in enumerate(words):
        if i in cue_set:
            result.append("[CUE]")
        result.append(word)
    return " ".join(result)
