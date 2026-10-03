# First run on a Windows PC without admin rights

Everything here installs for your own user account only. Do the steps in order and
stop at the first one that fails: the failure message is what to send back.

## 1. Install the tools (no administrator needed)

1. **Python 3.11** from python.org. Tick **Install for current user** and **Add python.exe to PATH**.
2. **Git for Windows** (the per-user install), or download the repository as a zip.
3. **FFmpeg**: download the "essentials" zip from gyan.dev, unzip it under your user folder, then add its
   `bin` folder to your **user** PATH (Start menu, "Edit environment variables for your account").
4. **Claude Code**: in PowerShell run `irm https://claude.ai/install.ps1 | iex`, open a new terminal, run
   `claude` once and log in with your Claude account. This is what lets the pipeline use your subscription.

## 2. Get the project and install it

```powershell
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking\manimgen
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
```

If `activate` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call
`.venv\Scripts\python.exe` directly instead of activating.

## 3. Three checks (about two minutes)

```powershell
python scripts\env_doctor.py       # tools, packages and config
python scripts\check_billing.py    # must print PASS: your subscription, extra usage off
python scripts\render_smoke.py     # OpenGL, a 480p render and a full-quality render, with timings
python scripts\tts_smoke.py        # real narration: needs internet access to Microsoft's speech service
```

If `tts_smoke.py` prints FAIL, narration is blocked on this network (see step 5 for the silent-draft fallback); send back its output.

Send back the table that `render_smoke.py` prints. Its two render times decide the open
questions about render time limits and crossfades (issue 75).

## 4. If the OpenGL check fails

Integrated graphics sometimes cannot create an OpenGL 3.3 context. The fallback needs no admin
rights: download `mesa3d-*-release-msvc.7z` from the pal1000/mesa-dist-win releases, unpack it with
a portable 7-Zip, and copy `x64\opengl32.dll`, `libgallium_wgl.dll` and `libglapi.dll` next to your
`python.exe` (`where python`). Then set `GALLIUM_DRIVER=llvmpipe`, `MESA_GL_VERSION_OVERRIDE=4.5` and
`MESA_GLSL_VERSION_OVERRIDE=450` and rerun `render_smoke.py`. This is the same recipe the nightly
Windows render job uses (see `.github/workflows/nightly-render.yml`), but it has only been proven on
GitHub's machines, not on a real library PC.

## 5. A real video

Set `rendering.quality: l` in `config.yaml` for a fast 480p draft, then:

```powershell
manimgen "binary search"
```

Narration needs internet access to Microsoft's speech service. If that is blocked, set
`tts.enabled: false` in `config.yaml` to get a silent draft. The finished video is in
`manimgen\output\videos`, next to a `run_manifest.json` that says what happened to each section.

## 6. Before you leave the computer

```powershell
claude /logout
deactivate
```

Sign out of claude.ai and GitHub in the browser, and close the browser. Copy anything you want to keep
to a USB drive or push it to GitHub: library machines often wipe files when you log out.
