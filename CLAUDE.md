# Sheet Music Assembler

This project builds a small tool that takes numbered sheet-music snippet
images (screenshots of individual lines/systems from a score, named like
`{SongName}_{n}.png`) and combines them into paginated, cleaned-up,
US-Letter-sized PDFs, ready to print.

The tool has two parts. **Do not merge them.** Image processing logic goes
in the script; the agent only orchestrates.

## 1. `scripts/assemble_sheet_music.py`

A standalone, deterministic Python CLI. No LLM calls, no guessing — every
run with the same inputs produces the same output. This is the part that
must be correct and tested, since an agent re-deriving this logic freehand
each time is exactly the failure mode we're avoiding.

### Responsibilities

**a. Input discovery & validation**
- Given a folder and a name prefix (e.g. `약할때_강함되시네`), find all
  files matching `{prefix}_{n}.png`.
- Confirm `n` values are contiguous starting at 1 with no gaps and no
  duplicates. If validation fails, exit with a clear message listing
  exactly what's missing or duplicated — never silently proceed.
- Normalize Unicode (NFC) before matching filenames, since uploaded
  filenames can arrive in different normalization forms.

**b. Page-count balancing**
Given a total piece count `N`, compute how many pieces go on each page:
- Max 6 pieces per page, min 4 pieces per page.
- No two pages should differ in piece count by more than 1.
- Formula: `num_pages = ceil(N / 6)`. Then distribute as evenly as
  possible: `base = N // num_pages`, `remainder = N % num_pages`.
  `remainder` pages get `base + 1` pieces, the rest get `base`.
- Verified examples (must match exactly):
  - N=11 → 2 pages: [6, 5]
  - N=15 → 3 pages: [5, 5, 5]
  - N=14 → 3 pages: [5, 5, 4]
- **Edge case:** this formula has exactly one gap — totals where no
  page count from 1 up satisfies `4*n <= N <= 6*n` for the chosen `n`.
  The only such value is **N=7** (1 page = 7 pieces, over max; 2 pages
  = 4+3, under min on one page). When this happens, don't guess — print
  the two options and let the caller/agent decide, or take an explicit
  `--pages` override (see below).
- Support a manual override flag `--pages "5,5,4"` (explicit comma list)
  for cases the caller wants to control directly, or for correcting the
  N=7 edge case.

**c. Optional insert (add a piece to an existing assembly)**
- `--insert <path> --at-top` (or `--at-position <page>:<index>`): adds
  one new piece image into the stack before running layout.
- Before stacking, measure the staff-line spacing of the inserted image
  and of a reference existing piece, and rescale the inserted image so
  spacing matches. Do this by:
  1. Convert to grayscale, compute per-row dark-pixel fraction.
  2. Find rows where that fraction exceeds a threshold (~0.5) — these
     are staff lines (full-width horizontal lines), as opposed to
     barlines (vertical, low per-row coverage).
  3. Group consecutive matching rows into line centers, take the median
     gap between adjacent centers as the staff spacing.
  4. Scale factor = `reference_spacing / inserted_spacing`.
  5. Resize the inserted image by that factor before stacking.
- This exists because a snippet re-exported from a different source
  (e.g. a higher-DPI PDF export) can be 2x+ the pixel size of the rest
  even though it "looks similar" in a thumbnail — verify by measurement,
  never assume matching source = matching scale.

**d. Stray-mark cleanup**
Snippets often carry incidental junk at their edges — bracket-tail lines,
barline/tie stubs, watermark logos in a corner — that aren't part of the
music but inflate the image's bounding box and create excess whitespace
when stacked. For each assembled page:
1. Build a dark-pixel mask (works for both black ink and colored logos —
   threshold on grayscale mean, not pure black).
2. Find column-groups and row-groups of connected ink (gap-tolerant
   grouping, ~25px gap threshold).
3. Identify the "main" group as the one with the most total ink.
4. Any other group that (a) touches within ~90px of the left/right edge
   or ~45px of the top/bottom edge, AND (b) doesn't overlap the main
   group's span, gets whitened out. Row-groups also require being thin
   (<18px tall) so we don't accidentally erase a real short system.
5. Crop tightly to the remaining content's bounding box.
- **Hard constraint: never modify pixels inside the main content
  region.** Only whitening isolated, edge-touching, out-of-band groups
  is allowed. When in doubt, don't remove it — leaving a stray mark is
  a minor cosmetic issue; damaging real notation is not acceptable.

**e. Uniform letter-page scaling**
- After building all pages for a run, compute ONE scale factor across
  ALL pages together (`scale = min(letter_width_minus_margin / max_page_width,
  letter_height_minus_margin / max_page_height)`), not a separate scale
  per page. All pages in one output must render notation at the same
  physical size — matching scale independently per page was tried and
  is wrong (it makes note size inconsistent between pages).
- Page size: US Letter, 8.5"×11" at 300 DPI = 2550×3300px.
- Margin: ~22px (adjustable via `--margin`), content centered.
- When adding a piece to an *existing* multi-page assembly (the insert
  case), recompute the uniform scale across all pages again, including
  the modified one — don't reuse the old scale.

**f. Output**
- Save each page as `{prefix}_page{n}.png` in the output directory.
- Combine all pages into a single `{prefix}.pdf` (RGB, 300 DPI).
- Print a short summary to stdout: page count, pieces-per-page
  breakdown, any warnings (e.g. stray-mark groups removed, insert
  rescale factor applied).

### CLI shape (adjust as needed while building, but keep it scriptable)

```
python assemble_sheet_music.py \
  --input-dir <folder> --prefix <SongName> --output-dir <folder> \
  [--pages "5,5,4"] \
  [--insert <path> --at-top] \
  [--margin 22]
```

### Testing

Write this with a few real test fixtures (small synthetic images are
fine — a handful of PNGs with fake staff lines at known spacing) and
unit tests for:
- Page-balancing formula against the three verified examples above,
  plus the N=7 edge case.
- Sequential-numbering validation (missing number, duplicate number).
- Staff-spacing measurement and rescale-factor calculation.
- Stray-mark isolation (a synthetic image with a real content block
  plus a deliberately isolated edge mark should end up with only the
  mark removed).

## 2. `.claude/agents/sheet-music-assembler.md`

A thin Claude Code subagent. Its entire job is orchestration and sanity
-checking — it must never reimplement the image logic above in bash or
Python inline. If the script is missing a capability the agent needs,
the agent should say so and stop, not improvise a workaround.

Responsibilities:
1. Locate the uploaded piece images (glob by prefix) and hand them to
   the script's validation step; surface any missing/duplicate pieces
   to the user before doing anything else.
2. If the user didn't specify a page layout, don't compute it manually
   — call the script without `--pages` and let it apply the balancing
   formula. If the script reports the N=7 gap case (or any ambiguity),
   ask the user how to split rather than picking for them.
3. Call the script with the resolved arguments.
4. View the resulting page PNGs itself (using the Read/view tool) and
   check for: no cropped staves, no obvious leftover stray marks, and
   staff size that looks consistent page-to-page. If something looks
   wrong, don't hand it to the user — retry with adjusted script flags
   or report the specific problem.
5. Present the final PDF and page images to the user with a one-line
   summary (page count and breakdown). No other commentary needed.

## Conventions

- All piece filenames use the pattern `{SongName}_{n}.png`, 1-indexed,
  Korean or English song names both allowed — always NFC-normalize
  before matching.
- Never alter musical content (notes, chords, lyrics, dynamics) under
  any circumstances. Every transformation in this tool is either
  layout (stacking, scaling, page breaks) or removal of content that is
  demonstrably NOT music notation (isolated edge marks, logos).
- When uncertain whether something is a stray mark or real notation,
  don't remove it.
