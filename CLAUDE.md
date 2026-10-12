# 3Blue1Brown — Project Index

The active project is **manimgen** — an automated pipeline that converts topics or PDFs into 3Blue1Brown-style animated videos.

## Session guide
See `manimgen/CLAUDE.md` — all pipeline context, API rules, known issues, and session state lives there. **Do not duplicate content here.**

## Repo
`https://github.com/Varkot-dev/videomaking.git`, branch `main` (the active branch; `antigravity` is historical)

## Project layout
```
videomaking/          ← git root
├── README.md         ← public docs (setup for macOS/Linux and Windows, LLM providers)
├── manimgen/         ← active project (pipeline, tests, scenes); run pip/pytest from here
├── manim/            ← ManimGL source submodule (read-only reference)
└── MASTER GUIDELINES.md
```

