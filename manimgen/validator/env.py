import os


def get_render_env() -> dict[str, str]:
    """
    Build a robust subprocess environment for manimgl rendering.
    Ensures TeX binaries are discoverable even when IDE shells do not load user profile files.
    """
    env = os.environ.copy()
    tex_bin = "/usr/local/texlive/2026basic/bin/universal-darwin"
    # Ensure subprocesses can resolve latex even if PATH is ignored by parent shell.
    env.setdefault("TEXLIVE_BIN", tex_bin)
    current_path = env.get("PATH", "")
    if tex_bin not in current_path.split(":"):
        env["PATH"] = f"{tex_bin}:{current_path}" if current_path else tex_bin
    # Also provide common shell startup hint for tools that inspect PATH helper variables.
    env.setdefault("MANIMGEN_LATEX", os.path.join(tex_bin, "latex"))
    return env
