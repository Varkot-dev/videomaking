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

If `tts_smoke.py` prints FAIL, narration is blocked on this network (see step 5 for the silent-draft fallback); send back its output. GitHub's machines got HTTP 403 from that service (issue #109), so whether it works here is unknown until this runs.

Send back the table that `render_smoke.py` prints. Its two render times decide the open
questions about render time limits and crossfades (issue 75).

## 4. If the OpenGL check fails

Read the `OpenGL` line of the `render_smoke.py` table first. It names the renderer that was used:

| What it says | Meaning | What to do |
|---|---|---|
| `PASS  GL 4.x on Intel(R) UHD Graphics 770` | The graphics chip works | Nothing, go to step 5 |
| `WARN  ... llvmpipe ... (software rendering)` | Works on the processor, slower | Nothing, or use quality `l` or `m` |
| `WARN  ... D3D12 (Microsoft Basic Render Driver) ... (software rendering)` | Works, but this is Microsoft's software adapter, so it is drawn on the processor, not the graphics chip | Nothing, or use quality `l` or `m` |
| `FAIL  could not create an OpenGL 3.3+ context` | The Intel driver cannot be used | Do the Mesa fallback below |

Mesa is a free OpenGL that can be dropped in without admin rights: you only copy three files. It
ships several drivers, chosen by the `GALLIUM_DRIVER` setting in step 4. Try them in this order:

| `GALLIUM_DRIVER` | What it does | Status |
|---|---|---|
| `d3d12` | Draws through DirectX 12, so it may use the real graphics chip | Try this first. On GitHub's Windows machine (no graphics chip) it worked, on Microsoft's software DirectX adapter. Whether it uses a real Intel chip is **not verified**. |
| `llvmpipe` | Draws on the processor | Always works, but slower. Use it if `d3d12` fails. |
| `zink` | Draws through Vulkan | Needs a Vulkan driver, usually not worth trying. It failed on GitHub's machine, which has no Vulkan driver; that says nothing about this PC. |

With the DLLs in place and no `GALLIUM_DRIVER` set at all, Mesa chose `d3d12` by itself on GitHub's
machine.

1. Download `mesa3d-*-release-msvc.7z` (the newest one) from the pal1000/mesa-dist-win releases
   page on GitHub. Unpack it with a portable 7-Zip (7zr.exe from 7-zip.org is one standalone file).
2. Find where Python runs from. In a virtual environment two folders matter, so copy into both:

   ```powershell
   python -c "import sys; print(sys.executable); print(sys.base_prefix)"
   ```

   The first line is `...\.venv\Scripts\python.exe`; its folder is the first target. The second
   line is the folder of the base `python.exe`; that is the second target.
3. Copy `opengl32.dll`, `libgallium_wgl.dll` and `libglapi.dll` from the unpacked `x64` folder into
   both folders (`libgallium_wgl.dll` and `libglapi.dll` only exist in newer Mesa releases; copy
   the ones that are there):

   ```powershell
   $mesa = "C:\path\to\unpacked\x64"
   $targets = ".venv\Scripts", (python -c "import sys; print(sys.base_prefix)")
   foreach ($t in $targets) { Copy-Item "$mesa\opengl32.dll", "$mesa\libgallium_wgl.dll", "$mesa\libglapi.dll" $t -Force -ErrorAction SilentlyContinue }
   ```

4. Pick a driver for this PowerShell window. Start with `d3d12`:

   ```powershell
   $env:GALLIUM_DRIVER = "d3d12"
   ```

   If step 5 fails with `d3d12`, use the processor instead:

   ```powershell
   $env:GALLIUM_DRIVER = "llvmpipe"
   $env:MESA_GL_VERSION_OVERRIDE = "4.5"
   $env:MESA_GLSL_VERSION_OVERRIDE = "450"
   ```

   These are forwarded to the render automatically. Set them again in each new window, or make them
   permanent for your own account (still no admin) with `setx GALLIUM_DRIVER d3d12` (and the same
   for the other two if you use `llvmpipe`), then open a new window.
5. Run `python scripts\render_smoke.py` again and read the `OpenGL` line, which names the renderer
   that ran:
   - `D3D12 (...)` means `d3d12` ran. The name in brackets is the adapter. `Microsoft Basic Render
     Driver` is Microsoft's software adapter, so no real graphics chip was used; an Intel name means
     the chip was used.
   - `llvmpipe` means the processor drew it (a `WARN` about software rendering is the expected,
     working result). Software drawing is slow, so set `rendering.quality: l` in `config.yaml` for drafts.

To undo it, delete the three DLLs from both folders and clear the variables (`Remove-Item
Env:GALLIUM_DRIVER`, or `setx GALLIUM_DRIVER ""` if you made it permanent). Windows then goes back to
the Intel driver.

What is proven: the DLL recipe passed on GitHub's Windows machines, which use one Python without a
virtual environment, and there `d3d12` and `llvmpipe` both gave OpenGL 4.5. Not proven: copying into
both folders for a virtual environment, `d3d12` on a PC with a real Intel graphics chip, and anything
about speed. If the `OpenGL` line still fails after step 5, send back the whole table.

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
