import hashlib
import json
import logging
import os
import re

from manimgen.llm import chat
from manimgen.planner.cue_parser import parse_cues
from manimgen.utils import sanitize_section_id
from manimgen.utils import strip_fencing as _strip_fencing

logger = logging.getLogger(__name__)

_MAX_SECTIONS_TOPIC = 6
_MAX_SECTIONS_PDF = 8


_SELF_CORRECT_LIMIT = 1  # number of critic passes per plan


def _load_critic_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(
        os.path.join(here, "prompts", "storyboard_critic_system.md"), encoding="utf-8"
    ) as f:
        return f.read()


def _self_correct(plan: dict, limit: int = _SELF_CORRECT_LIMIT) -> dict:
    critic_system = _load_critic_system_prompt()
    for _ in range(limit):
        raw = chat(
            system=critic_system, user=json.dumps(plan, indent=2), json_mode=True
        )
        try:
            plan = _parse_plan_json(raw)
        except Exception:
            logger.warning(
                "[planner] Self-correction returned non-JSON — keeping original plan"
            )
            break
    return plan


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(
        os.path.join(here, "prompts", "planner_system.md"), encoding="utf-8"
    ) as f:
        return f.read()


def _load_pdf_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(
        os.path.join(here, "prompts", "planner_pdf_system.md"), encoding="utf-8"
    ) as f:
        return f.read()


def _load_researcher_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(
        os.path.join(here, "prompts", "researcher_system.md"), encoding="utf-8"
    ) as f:
        return f.read()


def _cap_sections(plan: dict, limit: int) -> dict:
    sections = plan.get("sections", [])
    if len(sections) > limit:
        logger.warning(
            "[planner] LLM returned %d sections, capping to %d", len(sections), limit
        )
        plan["sections"] = sections[:limit]
    return plan


def _sanitize_section_ids(plan: dict) -> dict:
    """Sanitize every ``section['id']`` in place at the parse boundary.

    This is the single trust boundary for the LLM-controlled planner JSON.
    Each id is coerced to a path/class-name-safe slug (see
    ``utils.sanitize_section_id``). When two sanitized ids collide (e.g. an
    attacker submits both ``a/b`` and ``a_b``, or two sections legitimately
    slug to the same value), a short content hash is appended so downstream
    file paths stay unique and one section cannot overwrite another's render.

    Sanitizing here means every downstream sink (TTS audio path, scene file,
    fallback file, muxed clip, render cache) receives an already-safe id; the
    per-sink ``safe_section_id`` calls are pure defense-in-depth for resume
    paths that bypass this function.
    """
    seen: set[str] = set()
    for idx, section in enumerate(plan.get("sections", []), start=1):
        if not isinstance(section, dict):
            continue
        raw = section.get("id")
        safe = sanitize_section_id(raw, idx)
        if safe in seen:
            suffix = hashlib.sha256(str(raw).encode()).hexdigest()[:6]
            safe = f"{safe[: 64 - 7]}_{suffix}"
        seen.add(safe)
        if safe != raw:
            logger.warning(
                "[planner] Sanitized section id %r -> %r (idx %d)", raw, safe, idx
            )
        section["id"] = safe
    return plan


def _extract_cues(plan: dict) -> dict:
    """Parse [CUE] markers from narration and merge with the cues[] storyboard array.

    After this runs each section has:
      - section["id"]                 sanitized, path/class-name-safe slug
      - section["narration"]          clean text (no [CUE] tags), ready for TTS
      - section["cue_word_indices"]   [0, 9, 23, ...] word indices from narration
      - section["cues"]               list of {index, visual} dicts from planner
                                      (synthesised from narration if planner omitted them)
    """
    # Security: sanitize untrusted LLM-controlled ids BEFORE they reach any
    # filesystem sink (path traversal → arbitrary .py write + manimgl exec).
    plan = _sanitize_section_ids(plan)
    for section in plan.get("sections", []):
        raw = section.get("narration", "")
        clean, indices = parse_cues(raw)
        section["narration"] = clean
        section["cue_word_indices"] = indices

        # Ensure section["cues"] exists and is indexed correctly.
        # cues[] must have len(indices) entries — one per segment (including the
        # opening segment at index 0, before the first [CUE]).
        existing_cues = section.get("cues", [])
        n_segments = len(indices)
        if len(existing_cues) != n_segments:
            logger.warning(
                "[planner] Section '%s' has %d cues[] but %d segments (%d [CUE] markers). "
                "Re-prompting planner to supply one visual per segment.",
                section.get("id", "?"),
                len(existing_cues),
                n_segments,
                n_segments - 1,
            )
            # Targeted recovery: ask the LLM for exactly n_segments visuals,
            # giving it the per-segment narration text. This produces real
            # content for the missing segment(s) instead of a blank-screen
            # placeholder. Falls through to the per-index synthesis below if
            # it fails or still returns the wrong count — never crashes.
            refilled = _refill_cues_via_llm(
                section.get("title", ""), clean, indices, existing_cues
            )
            if refilled is not None:
                existing_cues = refilled

        title = section.get("title", "")
        # The per-segment narration is the PRIMARY, always-answerable input for
        # any missing visual: every segment has its own words, so a content-free
        # "animate segment N of M" placeholder is never needed. This eliminates
        # the count-mismatch failure mode at its source — the Director always
        # receives a visual grounded in the exact narration it must animate over.
        segment_texts = _segment_narration(clean, indices)
        cues_out = []
        for i in range(n_segments):
            # A visual counts as present only if it's a non-empty string.
            # An empty/whitespace-only "visual" would otherwise reach the
            # Director as-is and yield a content-free scene; fall through to
            # the narration-derived visual instead.
            has_visual = (
                i < len(existing_cues)
                and isinstance(existing_cues[i].get("visual"), str)
                and existing_cues[i]["visual"].strip() != ""
            )
            if has_visual:
                entry = dict(existing_cues[i])
                entry["index"] = i
                # Reconstruct real LaTeX from the backslash-free `§` notation
                # the planner is instructed to emit (§ cannot break JSON the
                # way a raw \ does). Single point where every cue is
                # normalized before any downstream consumer (Director prompt
                # build, example selection, 3D promotion) sees it.
                entry["visual"] = _reconstruct_latex(entry["visual"])
                cues_out.append(entry)
            else:
                # No planner-supplied visual for this segment. Derive one from
                # the segment's OWN narration so the Director animates the
                # actual spoken words rather than a generic placeholder.
                fallback = _narration_derived_visual(
                    title, segment_texts[i] if i < len(segment_texts) else ""
                )
                logger.warning(
                    "[planner] Section '%s' cue %d has no visual — deriving from "
                    "segment narration: %r",
                    section.get("id", "?"),
                    i,
                    fallback,
                )
                cues_out.append({"index": i, "visual": fallback})
        section["cues"] = cues_out

        if len(indices) == 1:
            logger.warning(
                "[planner] Section '%s' has no [CUE] markers — single animation segment",
                section.get("id", "?"),
            )
    return plan


def _narration_derived_visual(title: str, segment_text: str) -> str:
    """Build a content-bearing cue visual from a segment's own narration.

    Replaces the old content-free ``"animate segment N of M"`` placeholder.
    The Director receives the exact words it must animate over, so even when
    the planner omits a cue the visual is still specific to the narration —
    never generic. Falls back to a section-level hint only when the segment
    text is empty (e.g. a bare [CUE] with no words after it).
    """
    text = segment_text.strip()
    if not text:
        return (
            f"Technique: title_reveal. Display the section title "
            f"{title!r} prominently while the narrator speaks."
        )
    return (
        f"Technique: narration_visual. Visualize this narration with a clear, "
        f"specific animation that illustrates exactly what is being said: "
        f"{text!r}"
    )


def _segment_narration(clean: str, indices: list[int]) -> list[str]:
    """Split clean narration into the N+1 segment texts using word indices.

    indices[k] is the word index where segment k starts. Segment k spans
    words[indices[k] : indices[k+1]] (last segment runs to the end).
    """
    words = clean.split()
    segs: list[str] = []
    for k, start in enumerate(indices):
        end = indices[k + 1] if k + 1 < len(indices) else len(words)
        segs.append(" ".join(words[start:end]).strip())
    return segs


def _refill_cues_via_llm(
    title: str,
    clean: str,
    indices: list[int],
    provided_cues: list[dict],
) -> list[dict] | None:
    """Re-prompt the planner for exactly len(indices) cues, one per segment.

    Returns a cues list of the correct length, or None if the call fails or
    still returns the wrong count (caller then uses placeholder synthesis).
    Bounded to a single LLM call — this is a safety net, not the hot path.
    """
    segments = _segment_narration(clean, indices)
    n = len(segments)
    seg_block = "\n".join(
        f"  segment {i} narration: {s!r}" for i, s in enumerate(segments)
    )
    provided_block = json.dumps([c.get("visual", "") for c in provided_cues], indent=2)
    user = (
        f"A section titled {title!r} has narration split into {n} segments by "
        f"[CUE] markers. Each segment needs exactly one visual. The planner "
        f"only supplied {len(provided_cues)} visual(s), so segments are "
        f"misaligned.\n\nSegments:\n{seg_block}\n\n"
        f"Visuals the planner already wrote (may be for the wrong segments):\n"
        f"{provided_block}\n\n"
        f"Return ONLY a JSON array of exactly {n} objects, in segment order: "
        f'[{{"index": 0, "visual": "..."}}, ...]. Each `visual` MUST start '
        f"with `Technique: <name>` and follow the same visual rules as the "
        f"main planner. Reuse/adapt the provided visuals where they fit a "
        f"segment; write new ones for segments that have none. Use § for "
        f"LaTeX backslashes inside Tex() only."
    )
    try:
        raw = chat(system=_load_system_prompt(), user=user, json_mode=True)
        # This path INTENTIONALLY accepts a top-level JSON array (the cue list),
        # so use the lenient parser rather than the dict-guaranteeing wrapper.
        parsed = _safe_json_loads_any(_strip_fencing(raw))
    except Exception as e:
        logger.warning("[planner] Cue refill LLM call failed: %s", e)
        return None

    cues = parsed if isinstance(parsed, list) else parsed.get("cues")
    if not isinstance(cues, list) or len(cues) != n:
        logger.warning(
            "[planner] Cue refill returned %s entries, expected %d — "
            "falling back to placeholder synthesis.",
            len(cues) if isinstance(cues, list) else "non-list",
            n,
        )
        return None
    return cues


# Sentinel the planner is instructed to use in place of a LaTeX backslash,
# because a raw "\" in a JSON string value silently corrupts or breaks
# json.loads (\f -> form-feed, \t -> tab, \x -> error). The planner emits
# "§frac{1}{x}"; we restore "\frac{1}{x}" here so the Director receives
# normal LaTeX exactly as before. See planner_system.md equations rule.
_LATEX_BACKSLASH_SENTINEL = "§"


def _reconstruct_latex(visual: str) -> str:
    """Restore real LaTeX backslashes from the planner's `§` sentinel.

    Idempotent for sentinel-free text (plain prose and the synthesised
    fallback contain no `§`, so they pass through unchanged).
    """
    return visual.replace(_LATEX_BACKSLASH_SENTINEL, "\\")


def _safe_json_loads_any(raw: str):
    """Parse LLM JSON, tolerating bare LaTeX backslashes. Returns whatever
    type the JSON encodes (dict, list, str, ...).

    The planner sometimes emits a top-level JSON array (e.g. a cue list from
    ``_refill_cues_via_llm``). Callers that need a specific shape must check
    it themselves; ``_safe_json_loads`` is the dict-guaranteeing wrapper.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_escape_bad_backslashes(raw))


def _safe_json_loads(raw: str) -> dict:
    """Parse LLM JSON and guarantee a ``dict`` for ``.get()``-style callers.

    ``json.loads`` can legitimately return a list, str, int, etc. — e.g. when
    the LLM ignores the schema and emits a top-level JSON array. Returning that
    unchecked would let callers' ``result.get(...)`` raise ``AttributeError``
    far from the cause. We fail fast here with a clear message instead.
    """
    result = _safe_json_loads_any(raw)
    if not isinstance(result, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(result).__name__} "
            f"(LLM returned a non-object top-level JSON value)"
        )
    return result


def _escape_bad_backslashes(s: str) -> str:
    """Escape backslashes that are not valid JSON escape sequences.

    JSON allows: \\\\ \\" \\/ \\b \\f \\n \\r \\t \\uXXXX
    LaTeX in LLM output often contains valid \\n, \\t but also
    \\theta, \\nabla, \\alpha etc. which are invalid JSON escapes.
    We walk the string and double-escape any backslash not followed by a
    valid JSON escape character.

    ``\\u`` is only a valid JSON escape when followed by EXACTLY four hex
    digits. LaTeX such as ``\\underbrace`` or ``\\union`` starts with ``\\u``
    too, and passing those through unescaped leaves ``json.loads`` to choke on
    the malformed ``\\uXXXX`` form. So ``\\u`` is treated as valid only when the
    next four characters are hex; otherwise it is double-escaped like any other
    bare backslash.
    """
    # Single-char escapes that are always valid after a backslash. ``u`` is
    # handled separately because it requires a 4-hex-digit suffix.
    simple_escapes = set('"\\\\/bfnrt')
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in simple_escapes:
                out.append(ch)  # keep valid single-char escape as-is
            elif nxt == "u" and _is_valid_unicode_escape(s, i):
                out.append(ch)  # keep valid \\uXXXX as-is
            else:
                out.append("\\\\")  # double-escape bare backslash
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _is_valid_unicode_escape(s: str, backslash_idx: int) -> bool:
    """True if ``s[backslash_idx:]`` begins a valid ``\\uXXXX`` escape.

    Requires a backslash at ``backslash_idx``, a ``u`` at the next position,
    and exactly four hex digits after that. ``\\uZZZZ`` or a truncated
    ``\\u12`` are rejected so the bare backslash gets escaped instead.
    """
    hex_start = backslash_idx + 2  # skip "\u"
    hex_end = hex_start + 4
    if hex_end > len(s):
        return False
    return all(c in _HEX_DIGITS for c in s[hex_start:hex_end])


# LaTeX commands that start with a letter JSON also accepts as an escape
# (\b \f \n \r \t). A reply like "\theta" or "\nabla" parses "successfully"
# into a tab or newline plus text, silently corrupting the Tex() string, so a
# backslash starting one of these is doubled before the first parse. Names are
# matched whole, so a real "\nNext line" escape is left alone.
_LATEX_ESCAPE_CLASH = frozenset(
    """neq ne nabla nu not notin neg newline nolimits ni
    rho right rightarrow rangle rceil rfloor rm rightleftharpoons
    text textbf textit textrm theta tau tan times to top triangle tilde tfrac
    therefore thinspace tag tiny tanh""".split()
)
_LETTER_RUN = re.compile(r"[A-Za-z]+")


def _protect_latex_backslashes(s: str) -> str:
    """Double backslashes that start LaTeX commands so JSON keeps them.

    Walks the text so an already-valid ``\\\\`` pair is never touched. A
    backslash followed by two or more letters is always a LaTeX command unless
    it is a JSON ``\\n``/``\\r``/``\\t`` escape glued to a word, which is told
    apart by matching the whole letter run against known commands.
    """
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = s[i + 1] if i + 1 < n else ""
        if nxt == "\\":
            out.append("\\\\")  # keep a valid escaped backslash as-is
            i += 2
            continue
        run = _LETTER_RUN.match(s, i + 1)
        word = run.group(0) if run else ""
        if len(word) >= 2 and (word[0] not in "nrt" or word in _LATEX_ESCAPE_CLASH):
            out.append("\\\\")  # LaTeX command: double the backslash
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _parse_plan_json(raw: str) -> dict:
    """Parse a planner reply into a plan dict, tolerating chatty replies.

    Takes the first JSON object found (``raw_decode`` from a ``{``), so prose
    before or after it, or around a code fence, is ignored. LaTeX backslashes
    are protected before the first parse. Raises ValueError when no object
    can be read.
    """
    text = _protect_latex_backslashes(raw)
    decoder = json.JSONDecoder()
    last_err: Exception | None = None
    pos = text.find("{")
    while pos != -1:
        try:
            obj, _ = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as e:
            last_err = e
            pos = text.find("{", pos + 1)
            continue
        if isinstance(obj, dict):
            return obj
        pos = text.find("{", pos + 1)
    raise ValueError(
        f"no JSON object found in the reply ({last_err or 'no opening brace'})"
    )


def _chat_plan(system: str, user: str, images: list[str] | None = None) -> dict:
    """Ask for a plan and parse it, re-asking exactly once on a bad reply.

    Both calls go through ``chat`` so the usage guard and budget apply.
    """
    raw = chat(system=system, user=user, images=images, json_mode=True)
    try:
        return _parse_plan_json(raw)
    except ValueError as e:
        logger.warning(
            "[planner] Plan reply was not usable JSON (%s) - re-asking once", e
        )
        retry_user = (
            f"{user}\n\nYour previous reply could not be used: {e}. "
            "Return ONLY the complete JSON object, with no text before or after it."
        )
        raw = chat(system=system, user=retry_user, images=images, json_mode=True)
        return _parse_plan_json(raw)


def research_topic(topic: str) -> dict:
    """Call LLM with researcher prompt to build a structured knowledge brief.

    Returns a dict with keys: topic, prerequisites, core_concepts, key_formulas,
    worked_example, failure_modes, real_world_connections, section_suggestions.
    """
    system = _load_researcher_system_prompt()
    raw = chat(
        system=system,
        user=f"Research this topic for an educational video: {topic}",
        json_mode=True,
    )
    try:
        brief = _safe_json_loads(_strip_fencing(raw))
        logger.info(
            "[planner] Research brief: %d core concepts, %d formulas",
            len(brief.get("core_concepts", [])),
            len(brief.get("key_formulas", [])),
        )
        return brief
    except Exception as e:
        logger.warning(
            "[planner] Failed to parse research brief: %s — continuing without research",
            e,
        )
        return {}


def _format_research_brief(brief: dict) -> str:
    """Format the research brief as source material for the planner prompt."""
    if not brief:
        return ""

    lines = ["--- RESEARCH BRIEF ---"]

    prerequisites = brief.get("prerequisites", [])
    if prerequisites:
        lines.append(f"Prerequisites: {', '.join(prerequisites)}")

    historical_context = brief.get("historical_context", "")
    if historical_context:
        lines.append(f"\nHistorical Context:\n  {historical_context}")

    t_vs_i = brief.get("textbook_vs_intuition", {})
    if t_vs_i:
        lines.append("\nTextbook vs Intuition:")
        if "textbook" in t_vs_i:
            lines.append(f"  Textbook: {t_vs_i['textbook']}")
        if "intuition" in t_vs_i:
            lines.append(f"  Intuition: {t_vs_i['intuition']}")

    perspectives = brief.get("multiple_perspectives", {})
    if perspectives:
        lines.append("\nMultiple Perspectives:")
        for k, v in perspectives.items():
            lines.append(f"  • {k.title()}: {v}")

    core_concepts = brief.get("core_concepts", [])
    if core_concepts:
        lines.append("\nCore concepts:")
        for c in core_concepts:
            name = c.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {c.get('explanation', '')}")
            if c.get("common_misconception"):
                lines.append(f"    Misconception: {c['common_misconception']}")
            if c.get("visual_opportunity"):
                lines.append(f"    Visual: {c['visual_opportunity']}")

    key_formulas = brief.get("key_formulas", [])
    if key_formulas:
        lines.append("\nKey formulas:")
        for f in key_formulas:
            name = f.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {f.get('formula', '')}")
            lines.append(f"    {f.get('explanation', '')}")

    worked_example = brief.get("worked_example", {})
    if worked_example:
        lines.append(f"\nWorked example: {worked_example.get('description', '')}")
        for step in worked_example.get("steps", []):
            lines.append(f"  {step}")

    failure_modes = brief.get("failure_modes", [])
    if failure_modes:
        lines.append("\nFailure modes / edge cases:")
        for fm in failure_modes:
            name = fm.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {fm.get('description', '')}")
            if fm.get("visual_opportunity"):
                lines.append(f"    Visual: {fm['visual_opportunity']}")

    real_world = brief.get("real_world_connections", [])
    if real_world:
        lines.append("\nReal-world connections: " + "; ".join(real_world))

    section_suggestions = brief.get("section_suggestions", [])
    if section_suggestions:
        lines.append("\nSuggested section flow:")
        for i, s in enumerate(section_suggestions, 1):
            lines.append(f"  {i}. {s}")

    lines.append("--- END RESEARCH BRIEF ---")
    return "\n".join(lines)


def plan_lesson(topic: str) -> dict:
    logger.info("[planner] Researching topic: %s", topic)
    brief = research_topic(topic)
    research_material = _format_research_brief(brief)

    system = _load_system_prompt()
    if research_material:
        user_message = f"Create a visual storyboard for: {topic}\n\n{research_material}"
    else:
        user_message = f"Create a visual storyboard for: {topic}"

    plan = _cap_sections(_chat_plan(system, user_message), _MAX_SECTIONS_TOPIC)
    plan = _self_correct(plan)
    # _self_correct wholesale-replaces `plan` with the critic LLM's output,
    # which can re-inflate section count past the cap. Re-assert the
    # invariant so "≤ _MAX_SECTIONS_TOPIC sections" holds unconditionally.
    plan = _cap_sections(plan, _MAX_SECTIONS_TOPIC)
    return _extract_cues(plan)


def plan_lesson_from_pdf(pdf_path: str) -> dict:
    from manimgen.input.pdf_parser import parse_pdf

    logger.info("[planner] Parsing PDF: %s", pdf_path)
    parsed = parse_pdf(pdf_path)
    images = parsed.get("images", [])

    if not parsed["raw_text"] and not images:
        raise ValueError(f"No content could be extracted from '{pdf_path}'.")

    logger.info(
        "[planner] Extracted %d pages, %d chunks, %d chars, %d image(s)",
        parsed["extracted_pages"],
        len(parsed["chunks"]),
        len(parsed["raw_text"]),
        len(images),
    )

    MAX_CHARS = 24_000
    content_parts = []
    total = 0
    for i, chunk in enumerate(parsed["chunks"]):
        entry = f"[Chunk {i + 1}]\n{chunk}"
        if total + len(entry) > MAX_CHARS:
            content_parts.append(
                f"[... {len(parsed['chunks']) - i} additional chunks truncated ...]"
            )
            break
        content_parts.append(entry)
        total += len(entry)

    source_content = "\n\n".join(content_parts)

    if source_content:
        user_message = (
            "Here is the extracted content from the lecture notes PDF. "
            "Create a visual storyboard based on this material.\n\n"
            f"--- SOURCE MATERIAL ---\n{source_content}\n--- END SOURCE MATERIAL ---"
        )
    else:
        user_message = (
            "Here are images extracted from a lecture notes PDF. "
            "Analyse all images and create a visual storyboard based on what you see."
        )

    MAX_IMAGES = 10
    if len(images) > MAX_IMAGES:
        images = images[:MAX_IMAGES]

    system = _load_pdf_system_prompt()
    logger.info(
        "[planner] Calling LLM for PDF lesson plan (images: %d)...", len(images)
    )
    plan = _cap_sections(
        _chat_plan(system, user_message, images if images else None),
        _MAX_SECTIONS_PDF,
    )
    plan = _self_correct(plan)
    # Same invariant as the topic path: _self_correct wholesale-replaces
    # `plan` with the critic LLM's output and can re-inflate section count
    # past the cap. Re-assert it.
    plan = _cap_sections(plan, _MAX_SECTIONS_PDF)
    return _extract_cues(plan)
