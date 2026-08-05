# Sheet Music Assembler

Combine numbered sheet-music snippet images (`{SongName}_{n}.png`) into
paginated, cleaned-up, US-Letter PDFs ready to print.

Two parts (see [CLAUDE.md](CLAUDE.md) for the full spec):

1. **`scripts/assemble_sheet_music.py`** — the deterministic image-processing CLI.
   All the real logic lives here: validation, page balancing, staff-spacing
   rescale on insert, stray-mark cleanup, uniform scaling, PDF export.
2. **`.claude/agents/sheet-music-assembler.md`** — a thin Claude Code subagent
   that only orchestrates the script and sanity-checks its output.

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

## Tests

```bash
uv run pytest
```
