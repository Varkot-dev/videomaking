"""Cross-platform behavior of the manimgl render environment (validator/env.py).

The render env used to hardcode a macOS TeX Live directory and join PATH with
":", which corrupts PATH on Windows (whose separator is ";") and drops the
variables a Windows child process needs to start at all.
"""

import os

from manimgen.validator import env as render_env


def _no_known_tex_dirs(monkeypatch):
    monkeypatch.setattr(render_env, "_KNOWN_TEX_BINS", ())


def test_path_joined_with_os_pathsep(monkeypatch, tmp_path):
    tex_dir = tmp_path / "texbin"
    tex_dir.mkdir()
    _no_known_tex_dirs(monkeypatch)
    monkeypatch.setattr(render_env, "_find_tex_bin", lambda path: str(tex_dir))
    monkeypatch.setenv("PATH", "/usr/bin")

    env = render_env.get_render_env()

    assert env["PATH"] == f"{tex_dir}{os.pathsep}/usr/bin"
    assert env["TEXLIVE_BIN"] == str(tex_dir)
    assert env["MANIMGEN_LATEX"] == os.path.join(str(tex_dir), "latex")


def test_tex_dir_already_on_path_is_not_duplicated(monkeypatch, tmp_path):
    tex_dir = str(tmp_path)
    monkeypatch.setattr(render_env, "_find_tex_bin", lambda path: tex_dir)
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}{tex_dir}")

    env = render_env.get_render_env()

    assert env["PATH"] == f"/usr/bin{os.pathsep}{tex_dir}"


def test_no_latex_leaves_path_untouched(monkeypatch):
    _no_known_tex_dirs(monkeypatch)
    monkeypatch.setattr(render_env.shutil, "which", lambda *a, **k: None)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("TEXLIVE_BIN", raising=False)
    monkeypatch.delenv("MANIMGEN_LATEX", raising=False)

    env = render_env.get_render_env()

    assert env["PATH"] == "/usr/bin"
    assert "TEXLIVE_BIN" not in env
    assert "MANIMGEN_LATEX" not in env


def test_latex_found_on_path(monkeypatch, tmp_path):
    latex = tmp_path / "latex"
    monkeypatch.setattr(
        render_env.shutil, "which", lambda name, path=None: str(latex)
    )
    assert render_env._find_tex_bin("/anything") == str(tmp_path)


def test_known_dir_used_when_latex_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(render_env.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(
        render_env, "_KNOWN_TEX_BINS", ("/does/not/exist", str(tmp_path))
    )
    assert render_env._find_tex_bin("") == str(tmp_path)


def test_windows_startup_vars_forwarded(monkeypatch):
    for name in ("SYSTEMROOT", "PATHEXT", "TEMP", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(name, f"value-{name}")

    env = render_env.get_render_env()

    for name in ("SYSTEMROOT", "PATHEXT", "TEMP", "USERPROFILE", "LOCALAPPDATA"):
        assert env.get(name) == f"value-{name}"
