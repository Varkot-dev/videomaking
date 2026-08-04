"""Mechanical accuracy checks for the project's prose documentation.

Documentation drifts because nothing fails when it goes stale. An audit of this
repo found nine independently-verifiable false claims in README.md and
CLAUDE.md — wrong test counts, references to files that had been renamed away,
advertised features with no implementing code, and personal absolute paths that
were both a privacy leak and a stale directory.

Every claim in that class shares one property: a machine can check it. This
module converts that class of error from "someone eventually notices" into a
red test. It deliberately does NOT try to check prose meaning — only the
mechanical, objectively-decidable claims:

  1. Every relative file path the docs cite actually exists on disk.
  2. No doc hardcodes a test count (they go stale within a day — point at the
     command instead).
  3. No tracked doc contains a personal home-directory absolute path.

When one of these fails, fix the documentation. Do not relax the check — the
check is the only thing standing between the repo and the next round of drift.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# The git repository root is one level above this Python package — the checkout
# is `videomaking/`, and this package lives in `videomaking/manimgen/`. That
# distinction matters: GitHub renders the README at the *git* root, so that file
# is the one visitors actually read.
GIT_ROOT = PACKAGE_ROOT.parent

REPO_ROOT = PACKAGE_ROOT

# Docs a reader is most likely to trust and act on, each paired with the
# directory its relative paths resolve against.
#
# The git-root README was originally omitted here, and that omission is exactly
# how the drift this module exists to catch went unnoticed: the package README
# was corrected while the one GitHub displays kept claiming 732 tests. A
# drift-detector that skips the most-read document is worse than none, because
# it reports "docs verified" while the visible ones rot.
PRIMARY_DOCS = (
    ("README.md", PACKAGE_ROOT),
    ("CLAUDE.md", PACKAGE_ROOT),
    ("README.md", GIT_ROOT),
)

# Backwards-compatible aliases for tests that only need the package docs.
PACKAGE_DOCS = ("README.md", "CLAUDE.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _tracked_files() -> tuple[Path, ...]:
    """Return every file git tracks, as absolute paths.

    Uses git rather than a glob so that generated output, virtualenvs, and
    caches are never scanned — only content that actually ships.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip("git ls-files unavailable; cannot enumerate tracked files")
    return tuple(REPO_ROOT / name for name in result.stdout.split("\0") if name)


def _tracked_docs() -> list[Path]:
    return [p for p in _tracked_files() if p.suffix.lower() == ".md"]


_PATH_SUFFIXES = r"(?:py|md|ya?ml|toml|txt|json|html|cfg|ini)"

# A backtick-quoted token that looks like a repo-relative file path:
# `validator/retry.py`, `docs/KNOWN_ISSUES.md`, `config.yaml`.
_PATH_IN_BACKTICKS = re.compile(
    rf"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.{_PATH_SUFFIXES})`"
)

# The target of a markdown link: [the plan](docs/PLAN.md).
#
# Backticks alone were not enough. A broken markdown link is the *most common*
# form of the drift this module exists to catch — it renders as an inviting
# hyperlink that 404s — and the original pattern sailed past it because the
# path was never backticked. Verified by planting
# [text](docs/DOES_NOT_EXIST.md), which passed before this was added.
#
# External links and in-page anchors are skipped: only repo-relative targets
# are checkable here.
_PATH_IN_MD_LINK = re.compile(
    rf"\]\((?!https?://|#|mailto:)([A-Za-z0-9_][A-Za-z0-9_./-]*\.{_PATH_SUFFIXES})"
    r"(?:#[^)]*)?\)"
)

# Paths that are illustrative rather than real: user-supplied inputs, generated
# output, and placeholders. Citing these is correct even though they are absent
# from a clean checkout.
_EXEMPT_PATH_PREFIXES = (
    "manimgen/output/",
    "output/",
    "path/to/",
    "<repo-root>",
)
_EXEMPT_PATH_EXACT = {
    "lecture.pdf",
    "notes.pdf",
    "file.py",
    "lesson.pdf",
    ".env",
    "requirements.txt",
    "setup.py",
    "editor.html",
    "templates/editor.html",
}


def _is_exempt(rel: str) -> bool:
    return rel in _EXEMPT_PATH_EXACT or rel.startswith(_EXEMPT_PATH_PREFIXES)


def _resolves(rel: str) -> bool:
    """True if a cited path exists somewhere sensible in the repo.

    Docs cite modules at three levels of qualification, all of them honest once
    the surrounding prose has established context:

      * repo-relative   — `manimgen/validator/retry.py`
      * package-relative — `validator/retry.py`
      * bare basename   — `codeguard.py`, `test_planner.py`

    The bare form is the interesting case: it must still name a file that
    actually exists, which is what caught `generator_system.md` and
    `rules_core.md`. So resolve it by searching tracked files for that
    basename rather than accepting it blindly.
    """
    if (REPO_ROOT / rel).exists():
        return True
    # Package-relative: `validator/retry.py` -> `manimgen/validator/retry.py`
    if (REPO_ROOT / "manimgen" / rel).exists():
        return True
    # Bare basename: accept only if some tracked file has exactly that name.
    if "/" not in rel:
        return any(p.name == rel for p in _tracked_files())
    return False


# ---------------------------------------------------------------------------
# 1. Cited paths must exist
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "doc_name,doc_root",
    PRIMARY_DOCS,
    ids=[f"{root.name}/{name}" for name, root in PRIMARY_DOCS],
)
def test_cited_file_paths_exist(doc_name: str, doc_root: Path) -> None:
    """Every file path a primary doc cites must exist on disk.

    Regression guard: the docs previously cited `generator_system.md`,
    `rules_core.md`, and `MASTER GUIDELINES.md`, none of which existed.

    Covers the git-root README as well as the package one — the git-root file is
    what GitHub renders, and omitting it is how a stale copy went unnoticed.
    """
    doc = doc_root / doc_name
    assert doc.exists(), f"{doc_name} is missing from {doc_root}"
    text = doc.read_text(encoding="utf-8")

    missing: list[str] = []
    # Both citation forms: backticked paths and markdown link targets. A broken
    # link is the more visible failure of the two, since it renders as a
    # hyperlink that 404s rather than as inert prose.
    for pattern in (_PATH_IN_BACKTICKS, _PATH_IN_MD_LINK):
        for match in pattern.finditer(text):
            rel = match.group(1)
            if _is_exempt(rel) or _resolves(rel):
                continue
            missing.append(rel)

    assert not missing, (
        f"{doc_name} cites file(s) that do not exist: {sorted(set(missing))}. "
        "Fix the path or drop the reference — do not add an exemption unless "
        "the path is genuinely illustrative (user input or generated output)."
    )


# ---------------------------------------------------------------------------
# 2. No hardcoded test counts
# ---------------------------------------------------------------------------


# "117 tests", "732 tests, all passing", "399 pytest tests", "857 passing".
_TEST_COUNT = re.compile(
    r"\b\d{2,}\s+(?:pytest\s+)?tests?\b|\b\d{2,}\s+(?:tests?\s+)?passing\b",
    re.IGNORECASE,
)


@pytest.mark.unit
def test_docs_do_not_hardcode_a_test_count() -> None:
    """No tracked doc may state a test count as a literal number.

    A transcribed count is stale the moment anyone adds a test, and this repo
    accumulated three mutually contradictory ones (117 / 399 / 732) against a
    real total of 857. The count has exactly one honest source: running the
    suite. Docs should print the command, not its output.
    """
    offenders: list[str] = []
    for doc in _tracked_docs():
        rel = doc.relative_to(REPO_ROOT).as_posix()
        # Historical records document what was true when written; freezing a
        # number there is the point, so they are exempt.
        if rel.startswith("docs/superpowers/") or rel.startswith("docs/ROOT_CAUSE"):
            continue
        if rel.startswith("SESSION_") or rel.startswith("docs/session"):
            continue
        for lineno, line in enumerate(
            doc.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _TEST_COUNT.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Documentation hardcodes a test count:\n  "
        + "\n  ".join(offenders)
        + "\nReplace the number with the command that produces it "
        "(`python3 -m pytest -q`)."
    )


# ---------------------------------------------------------------------------
# 3. No personal absolute paths
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_no_tracked_file_leaks_a_home_directory_path() -> None:
    """No tracked file may contain a `/Users/<name>` or `/home/<name>` path.

    Two problems at once: it publishes the author's local directory layout in a
    public repo, and it is a stale path that no longer matches where the project
    lives — so a reader who copies the `cd` line lands nowhere. Use a relative
    path or a `<repo-root>` placeholder.
    """
    leak = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")

    offenders: list[str] = []
    for path in _tracked_files():
        # This file necessarily contains the pattern it searches for.
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — nothing to leak in prose
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if leak.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")

    assert not offenders, (
        "Tracked file(s) contain a personal home-directory path:\n  "
        + "\n  ".join(offenders)
        + "\nReplace with a relative path or the `<repo-root>` placeholder."
    )


# ---------------------------------------------------------------------------
# 4. Specific claims that were previously false
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_docs_do_not_recommend_ignore_flags_for_pytest() -> None:
    """`pyproject.toml` forbids default `--ignore` flags; docs must agree.

    CLAUDE.md twice published a test command carrying the exact `--ignore` trio
    that pyproject.toml's header documents as harmful. A reader following the
    docs would reintroduce the local/CI divergence the config exists to prevent.
    """
    # Match an actual invocation: a pytest command line carrying `--ignore=`.
    # Prose *about* the flag ("do not add `--ignore` flags") is the correct
    # thing for a doc to say, so it must not trip this check — hence requiring
    # the `=` that only a real argument has.
    invocation = re.compile(r"pytest\b[^\n`]*--ignore=")

    offenders: list[str] = []
    for doc_name, doc_root in PRIMARY_DOCS:
        text = (doc_root / doc_name).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if invocation.search(line):
                offenders.append(f"{doc_root.name}/{doc_name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Documentation recommends a pytest invocation with --ignore flags:\n  "
        + "\n  ".join(offenders)
        + "\npyproject.toml requires a bare `python3 -m pytest -q`."
    )


@pytest.mark.unit
def test_readme_example_count_matches_examples_dir() -> None:
    """If README states a count of example scenes, it must be the real one.

    README claimed "5 hand-written ManimGL scenes" against an `examples/`
    directory holding 32.
    """
    actual = len(list((REPO_ROOT / "examples").glob("*.py")))
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    stated = re.findall(
        r"(\d+)\s+hand-written(?:\s+verified)?\s+ManimGL\s+scenes", text
    )
    for value in stated:
        assert int(value) == actual, (
            f"README says {value} hand-written ManimGL scenes but examples/ "
            f"contains {actual} .py files."
        )


@pytest.mark.unit
def test_muxer_docs_do_not_claim_unimplemented_ffmpeg_features() -> None:
    """Docs must not credit the muxer with speed-warping or looping.

    README described `setpts=` video speed-up and `-stream_loop -1` looping as
    live strategies. Neither token appears in muxer.py — it pads audio with
    `apad` and freezes the last frame with `tpad`. Guard both directions so the
    claim can only be re-added alongside a real implementation.
    """
    muxer_src = (REPO_ROOT / "manimgen" / "renderer" / "muxer.py").read_text(
        encoding="utf-8"
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for token in ("setpts", "stream_loop"):
        if token in readme:
            assert token in muxer_src, (
                f"README references `{token}` but muxer.py does not implement "
                "it. Describe the pad/freeze behaviour the code actually has."
            )


@pytest.mark.unit
def test_config_does_not_advertise_unimplemented_tts_engines() -> None:
    """config.yaml must not offer TTS engines with no implementing code.

    The `tts.engine` key listed `edge-tts | elevenlabs | openai`. renderer/tts.py
    never reads the key and imports edge_tts unconditionally, so two thirds of
    that menu did nothing. If an alternative engine is ever offered without
    being marked unimplemented, tts.py must actually dispatch on the key.
    """
    tts_src = (REPO_ROOT / "manimgen" / "renderer" / "tts.py").read_text(
        encoding="utf-8"
    )
    config_text = (REPO_ROOT / "config.yaml").read_text(encoding="utf-8")

    engine_line = next(
        (
            line
            for line in config_text.splitlines()
            if line.strip().startswith("engine:")
        ),
        None,
    )
    assert engine_line is not None, "config.yaml no longer defines tts.engine"

    for provider in ("elevenlabs", "openai"):
        if provider in engine_line:
            assert provider in tts_src, (
                f"config.yaml offers the {provider!r} TTS engine on the "
                f"`engine:` line, but renderer/tts.py has no {provider} "
                "implementation. Mark it UNIMPLEMENTED in a comment or "
                "implement it."
            )
