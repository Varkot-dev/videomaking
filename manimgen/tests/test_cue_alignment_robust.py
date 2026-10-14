"""Robustness tests for cue_parser.align_cue_indices (CLAUDE.md known issue #6).

parse_cues counts words with str.split(); edge-tts WordBoundary events are the
canonical clock and can tokenize differently. These cases cover divergences
the original token-walking aligner got wrong:

  * equal token COUNTS that still disagree (a spaced dash plus a hyphenated
    word that edge-tts splits cancel out in the count);
  * edge-tts merging two split words into one boundary;
  * edge-tts reporting different content (a number read back as words);
  * punctuation-only tokens on either side.

Plus a hypothesis property: for any narration and any way edge-tts re-breaks
it (splitting at hyphens, dropping punctuation-only tokens), every cue lands
on the edge-tts token where the intended word starts.
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from manimgen.planner.cue_parser import align_cue_indices, parse_cues
from manimgen.planner.segmenter import compute_segments
from manimgen.renderer.tts import WordTimestamp


def test_equal_counts_but_shifted_tokens_are_realigned():
    # split:  x  -  y  well-known  z   (5 tokens, cue before "y" = index 2)
    # tts:    x  y  well  known  z     (5 tokens, index 2 is "well": wrong)
    clean, cues = parse_cues("x - [CUE] y well-known z")
    assert cues == [0, 2]
    tts = ["x", "y", "well", "known", "z"]
    assert len(tts) == len(clean.split())
    aligned = align_cue_indices(clean, cues, tts)
    assert [tts[i] for i in aligned] == ["x", "y"]


def test_punctuation_only_split_token_before_cue():
    clean, cues = parse_cues("First idea — [CUE] second idea")
    tts = ["First", "idea", "second", "idea"]
    aligned = align_cue_indices(clean, cues, tts)
    assert [tts[i] for i in aligned] == ["First", "second"]


def test_tts_merges_two_split_words():
    # edge-tts reporting "New York" as one boundary must not push every later
    # cue one word too far.
    clean = "We fly to New York then on to Boston today"
    tts = ["We", "fly", "to", "New York", "then", "on", "to", "Boston", "today"]
    aligned = align_cue_indices(clean, [0, 5, 8], tts)  # "then", "Boston"
    assert [tts[i] for i in aligned] == ["We", "then", "Boston"]


def test_tts_content_differs_number_read_as_words():
    clean = "There are 16 items and then we stop"
    tts = ["There", "are", "sixteen", "items", "and", "then", "we", "stop"]
    tts = tts[:3] + ["extra"] + tts[3:]  # force a count mismatch too
    aligned = align_cue_indices(clean, [0, 3, 5], tts)  # "items", "then"
    assert [tts[i] for i in aligned] == ["There", "items", "then"]


def test_punctuation_only_tts_tokens_are_never_chosen():
    clean = "Alpha beta gamma delta"
    tts = ["Alpha", "beta", ",", "gamma", ".", "delta"]
    aligned = align_cue_indices(clean, [0, 2, 3], tts)
    assert [tts[i] for i in aligned] == ["Alpha", "gamma", "delta"]


def test_compute_segments_uses_realigned_index_for_equal_counts():
    clean, cues = parse_cues("x - [CUE] y well-known z")
    words = ["x", "y", "well", "known", "z"]
    timestamps = [
        WordTimestamp(word=w, start=float(i), end=i + 0.5) for i, w in enumerate(words)
    ]
    segments = compute_segments(timestamps, cues, 6.0, clean_text=clean)
    # Cue 1 starts on "y" (t=1.0), not on "well" (t=2.0).
    assert segments[1].start_time == 1.0
    assert segments[0].duration == 0.5  # ends at "x".end


# ---------------------------------------------------------------------------
# Property: any edge-tts re-breaking of the same words keeps cues on target.
# ---------------------------------------------------------------------------

_WORD = st.text(alphabet="abcdefghij", min_size=1, max_size=6)
_PIECE = st.one_of(
    _WORD,
    st.tuples(_WORD, _WORD).map(lambda p: f"{p[0]}-{p[1]}"),  # hyphenated
    _WORD.map(lambda w: w + ","),  # trailing punctuation
    st.just("—"),  # spaced dash: a punctuation-only split token
)


def _edge_like(words: list[str]) -> list[tuple[str, int]]:
    """Tokenize like edge-tts: drop punctuation-only, split hyphens.

    Returns (token, index of the split word it came from).
    """
    out = []
    for i, raw in enumerate(words):
        for part in re.split(r"-", raw):
            core = re.sub(r"[^a-z]", "", part)
            if core:
                out.append((core, i))
    return out


@settings(max_examples=200, deadline=None)
@given(pieces=st.lists(_PIECE, min_size=1, max_size=25), data=st.data())
def test_alignment_property(pieces, data):
    words = pieces
    edge = _edge_like(words)
    if not edge:
        return
    tts = [t for t, _ in edge]
    # Cue candidates: split indices whose word has spoken content.
    spoken = sorted({i for _, i in edge})
    chosen = data.draw(st.lists(st.sampled_from(spoken), unique=True, max_size=5))
    cues = [0] + sorted(i for i in chosen if i > 0)
    aligned = align_cue_indices(" ".join(words), cues, tts)

    assert aligned[0] == 0
    assert aligned == sorted(aligned)
    assert all(0 <= i < len(tts) for i in aligned)
    for split_idx, tts_idx in zip(cues[1:], aligned[1:]):
        first_token = next(j for j, (_, src) in enumerate(edge) if src == split_idx)
        assert tts_idx == first_token
