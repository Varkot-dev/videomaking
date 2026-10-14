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

import difflib
import logging
import re

logger = logging.getLogger(__name__)

_CUE_TAG = re.compile(r"\[CUE\]", re.IGNORECASE)

# Keep only word characters when comparing a str.split() token against the
# edge-tts WordBoundary token. edge-tts attaches/strips punctuation
# differently ("you'll" vs "you ' ll", "16" vs "16,"), so we align on the
# concatenated alphanumeric content rather than on exact token equality.
_NON_WORD = re.compile(r"[^0-9a-z]+")


def _word_key(token: str) -> str:
    """Normalise a token to its lowercase alphanumeric content for alignment."""
    return _NON_WORD.sub("", token.lower())


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


def align_cue_indices(
    clean_text: str,
    split_indices: list[int],
    tts_words: list[str],
) -> list[int]:
    """Re-derive cue word indices against the CANONICAL edge-tts tokenization.

    ``parse_cues`` counts words with ``str.split()``, but edge-tts WordBoundary
    events tokenize differently — contractions ("you'll"), numbers ("16,"), and
    hyphenated words ("well-known") may split into a different number of tokens.
    A ``cue_word_indices`` value that is still *in range* but shifted silently
    resolves to the WRONG word's onset in ``tts.cue_times`` → inaudible A/V
    desync with no error.

    This maps each str.split() boundary index to the index of the edge-tts
    token that begins the same word, by aligning the concatenated alphanumeric
    content (``_word_key``) of both streams character by character. The edge-tts
    stream is the canonical clock, so the returned indices are what
    ``cue_times`` / ``compute_segments`` must use.

    Args:
        clean_text:    The narration that was sent to TTS (no [CUE] tags).
        split_indices: cue_word_indices from ``parse_cues`` (str.split based).
        tts_words:     The ordered words from edge-tts WordBoundary events.

    Returns:
        Cue indices into ``tts_words``. Always starts at 0, is non-decreasing,
        and every value is a valid index into ``tts_words`` (clamped). When the
        two tokenizations agree this is a no-op (returns ``split_indices``).
    """
    split_words = clean_text.split()
    split_keys = [_word_key(w) for w in split_words]
    tts_keys = [_word_key(w) for w in tts_words]

    # Fast path: the two streams agree token for token, nothing to do. Equal
    # token COUNTS are not enough: a punctuation-only split token (a spaced
    # dash) and a hyphenated word edge-tts splits in two cancel out in the
    # count while every index between them is shifted by one.
    if split_keys == tts_keys:
        return list(split_indices)

    logger.warning(
        "[cue_parser] Tokenization mismatch: str.split()=%d words, edge-tts "
        "WordBoundary=%d words. Re-deriving cue indices against the edge-tts "
        "stream (canonical) to prevent silent A/V desync.",
        len(split_words),
        len(tts_words),
    )

    if not tts_words:
        # No canonical stream to align against: every cue collapses to 0.
        return [0 for _ in split_indices]

    # Align on character positions, not token positions. Each split word is
    # located by the offset of its first alphanumeric character in the
    # concatenated content, that offset is mapped into the edge-tts content,
    # and the edge-tts token covering it is the cue's canonical index. This is
    # indifferent to how either side breaks words (hyphens, contractions,
    # punctuation-only tokens, merged tokens).
    n_tts = len(tts_words)
    split_starts = _key_offsets(split_keys)
    tts_ends = [
        start + len(key) for start, key in zip(_key_offsets(tts_keys), tts_keys)
    ]
    char_map = _CharMap("".join(split_keys), "".join(tts_keys))

    def _resolve(split_idx: int) -> int:
        if split_idx <= 0:
            return 0
        if split_idx >= len(split_words):
            # Boundary past the last word: end of stream.
            return n_tts - 1
        pos = char_map.map(split_starts[split_idx])
        # The edge-tts token containing this character, or the first one after
        # it. Content-free tokens (pure punctuation) have an empty span and are
        # never chosen.
        for j, end in enumerate(tts_ends):
            if end > pos:
                return j
        return n_tts - 1

    aligned = []
    last = 0
    for idx in split_indices:
        resolved = max(_resolve(idx), last)  # keep non-decreasing
        resolved = max(0, min(resolved, n_tts - 1))  # clamp into range
        aligned.append(resolved)
        last = resolved
    return aligned


def _key_offsets(keys: list[str]) -> list[int]:
    """Start offset of each key inside ``"".join(keys)``."""
    offsets = []
    pos = 0
    for key in keys:
        offsets.append(pos)
        pos += len(key)
    return offsets


class _CharMap:
    """Map character positions in one normalised text onto another.

    Both texts are the concatenated ``_word_key`` content of a token stream,
    so they are identical whenever the two tokenizers only disagree on where
    words break. When the content itself differs (a number read back as words,
    a dropped symbol), the matching blocks from ``difflib`` anchor the mapping
    and an unmatched position snaps to the next matched character, so one
    local difference never shifts the rest of the narration.
    """

    def __init__(self, source: str, target: str):
        self._identical = source == target
        self._target_len = len(target)
        self._blocks = []
        if not self._identical:
            matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
            self._blocks = [b for b in matcher.get_matching_blocks() if b.size]

    def map(self, pos: int) -> int:
        if self._identical:
            return pos
        for a, b, size in self._blocks:
            if pos < a:
                return b  # unmatched: snap to the next matched character
            if pos < a + size:
                return b + (pos - a)
        return self._target_len


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
