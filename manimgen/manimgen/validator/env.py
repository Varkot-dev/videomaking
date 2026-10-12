import os
import shutil

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
        # X11 cannot connect to the server (including a headless Xvfb) without
        # the cookie file this points at. It is a path, not a secret.
        "XAUTHORITY",
        # Windows: Python and OpenGL fail to start in a child process without
        # SYSTEMROOT/WINDIR, PATHEXT is how "manimgl"/"latex" resolve to .exe,
        # and TEMP/TMP/USERPROFILE/APPDATA are the Windows HOME/TMPDIR.
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
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
    # Mesa software OpenGL (llvmpipe) selection. This is the fallback on a
    # machine whose GPU driver cannot create an OpenGL 3.3 context.
    "MESA_",
    "GALLIUM_",
    "LIBGL_",
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

    tex_bin = _find_tex_bin(env.get("PATH", ""))
    if tex_bin:
        # Ensure subprocesses can resolve latex even if PATH is ignored by parent shell.
        env.setdefault("TEXLIVE_BIN", tex_bin)
        current_path = env.get("PATH", "")
        if tex_bin not in current_path.split(os.pathsep):
            env["PATH"] = (
                f"{tex_bin}{os.pathsep}{current_path}" if current_path else tex_bin
            )
        # Also provide common shell startup hint for tools that inspect PATH helper variables.
        env.setdefault("MANIMGEN_LATEX", os.path.join(tex_bin, "latex"))
    return env


# Install locations that are not always on PATH (macOS GUI apps and IDE shells
# skip the profile that adds them). Only directories that exist are used, so
# these are harmless on platforms where they do not apply.
_KNOWN_TEX_BINS = (
    "/usr/local/texlive/2026basic/bin/universal-darwin",
    "/Library/TeX/texbin",
)


def _find_tex_bin(path: str) -> str | None:
    """Return the directory holding ``latex``, or None if it cannot be found.

    PATH is searched first (covers Linux, Windows/MiKTeX and a correctly set up
    macOS), then the known macOS install locations.
    """
    found = shutil.which("latex", path=path or None)
    if found:
        return os.path.dirname(found)
    for candidate in _KNOWN_TEX_BINS:
        if os.path.isdir(candidate):
            return candidate
    return None
