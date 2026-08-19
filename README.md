# Sheet Music Assembler

Combine numbered sheet-music snippet images (`{SongName}_{n}.png`) into
paginated, cleaned-up, US-Letter PDFs ready to print.

Three parts (see [CLAUDE.md](CLAUDE.md) for the full spec). The image logic lives
in the CLI and is never reimplemented — the subagent and the web UI are both thin
consumers of it:

1. **`scripts/assemble_sheet_music.py`** — the deterministic image-processing CLI.
   All the real logic lives here: validation, page balancing, staff-spacing
   rescale on insert, stray-mark cleanup, uniform scaling, PDF export.
2. **`.claude/agents/sheet-music-assembler.md`** — a thin Claude Code subagent
   that only orchestrates the script and sanity-checks its output.
3. **`web/`** — a local FastAPI + React/Vite web UI: drag snippets into the browser,
   assemble, preview the pages, download the PDF. Imports the CLI; never re-derives
   its logic.

## Setup

```bash
uv sync
```

## Usage

Drop your snippets into `input/` named `{SongName}_1.png`, `{SongName}_2.png`, …
(1-indexed, contiguous; Korean or English names both fine), then:

```bash
uv run python scripts/assemble_sheet_music.py \
  --input-dir input --prefix "약할때_강함되시네" --output-dir output
```

Outputs `output/{prefix}_page{n}.png` and a combined `output/{prefix}.pdf`.

### Options

- `--pages "5,5,4"` — override the automatic page split (also used to resolve the
  N=7 edge case, which the tool refuses to guess on).
- `--insert <path> --at-top` — add one extra piece at the top before layout; the
  tool measures staff spacing and rescales the inserted image to match.
- `--insert <path> --at-position <page>:<index>` — insert at a spot in the current
  layout instead.
- `--margin 22` — page margin in pixels (300 DPI, US Letter 2550×3300).

## Via the subagent

Ask Claude Code to use the **sheet-music-assembler** agent; it will validate the
sequence, run the script, view the output pages, and hand you the PDF.

## Web UI

A local browser front end for the same engine — no accounts, runs on your machine.

```bash
uv sync --extra web                                  # one-time: install backend deps
uv run uvicorn web.backend.app:app --port 8000       # terminal 1: API
cd web/frontend && npm install && npm run dev         # terminal 2: UI (first run: npm install)
```

Open the printed localhost URL, drag your numbered PNGs in, and assemble. Adjust the
margin or page split and re-assemble to update the preview, then download the PDF.
Uploads live in a temp session, so re-running with new settings doesn't need a
re-upload.

## Tests

```bash
uv run pytest tests web/backend/tests   # Python: CLI core + web backend
cd web/frontend && npm test             # frontend (Vitest)
```

A bare `uv run pytest` only runs the CLI core suite (`testpaths = ["tests"]`); pass
`web/backend/tests` explicitly to include the backend tests.
