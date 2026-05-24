import os

# Allowlist of environment variable NAMES that may be forwarded to the manimgl
# render subprocess. Generated scene code runs *inside* manimgl and can read
# os.environ, so copying the full parent environment leaks secrets such as
# GEMINI_API_KEY / ANTHROPIC_API_KEY to untrusted, LLM-authored code.
#
# This is an ALLOWLIST, not a denylist: a denylist rots the moment a new secret
# env var is introduced. Only names matching one of these exact strings or
# prefixes are passed through. Anything the renderer genuinely needs beyond
# this set can be added via the MANIMGEN_RENDER_ENV_EXTRA escape hatch
# (comma-separated variable names).
_ALLOWED_EXACT = frozenset(
    {
        "PATH",  # locate manimgl, ffmpeg, latex, python
        "HOME",  # config/cache dirs (manimgl, matplotlib, fonts)
        "TMPDIR",  # temp render artifacts
        "LANG",  # locale (text rendering)
        "DISPLAY",  # X11 / OpenGL context discovery
        # Vars get_render_env() itself sets for LaTeX discoverability:
        "TEXLIVE_BIN",
        "MANIMGEN_LATEX",
    }
)

# Prefix families: every var whose name starts with one of these is allowed.
_ALLOWED_PREFIXES = (
    "LC_",  # locale categories (LC_ALL, LC_CTYPE, ...)
    "TEXLIVE_",  # TeX Live install/runtime config
    "PYTHON",  # PYTHONPATH, PYTHONHOME, PYTHONNOUSERSITE, ...
)

_EXTRA_ENV_VAR = "MANIMGEN_RENDER_ENV_EXTRA"


def _extra_allowed_names() -> set[str]:
    """Parse the comma-separated escape-hatch var into a set of names."""
    raw = os.environ.get(_EXTRA_ENV_VAR, "")
    return {name.strip() for name in raw.split(",") if name.strip()}


def _is_allowed(name: str, extra: set[str]) -> bool:
    if name in _ALLOWED_EXACT or name in extra:
        return True
    return name.startswith(_ALLOWED_PREFIXES)


def get_render_env() -> dict[str, str]:
    """
    Build a minimal, allowlisted subprocess environment for manimgl rendering.

    Only an explicit allowlist of variables is forwarded from the parent
    environment, so API keys and other secrets are never exposed to the
    LLM-authored scene code that manimgl executes. TeX binaries are still made
    discoverable even when IDE shells do not load user profile files.
    """
    extra = _extra_allowed_names()
    env = {
        name: value for name, value in os.environ.items() if _is_allowed(name, extra)
    }

    tex_bin = "/usr/local/texlive/2026basic/bin/universal-darwin"
    # Ensure subprocesses can resolve latex even if PATH is ignored by parent shell.
    env.setdefault("TEXLIVE_BIN", tex_bin)
    current_path = env.get("PATH", "")
    if tex_bin not in current_path.split(":"):
        env["PATH"] = f"{tex_bin}:{current_path}" if current_path else tex_bin
    # Also provide common shell startup hint for tools that inspect PATH helper variables.
    env.setdefault("MANIMGEN_LATEX", os.path.join(tex_bin, "latex"))
    return env
