# Sheet Music Assembler — Uniform Staff Width Normalization

**Date:** 2026-09-04
**Status:** Approved (design), pending implementation plan
**Type:** Algorithm change to the deterministic CLI (`scripts/assemble_sheet_music.py`), no AI

## Purpose

Real snippet sets vary in **staff length** (system width), not just note size: pieces
screenshotted at different resolutions, or with genuinely different staff structures
(e.g. a piano-only excerpt vs. a piano+vocal system), end up with visibly different
system widths once stacked into a page. This doesn't look like real sheet music —
printed/engraved scores keep every system on a page the same width, letting note
spacing stretch or compress slightly to fill the line.

This spec replaces the whole-set normalization's scaling basis from **staff-line
spacing** (note size) to **content width** (staff length). Confirmed against a real
snippet set during testing of the height-based page-packing feature: an 11-piece
Korean worship lead sheet where a piano-only excerpt (2 staves, no vocal line) was
captured at ~800px wide against ~1450-1470px for the piano+vocal pieces — same
measure density per line, wildly different pixel width.

## Scope

**In:** replace `normalize_piece_scales`'s internal measurement from
`measure_staff_spacing` to a new `measure_content_width`; rescale every piece to the
median content width instead of median staff spacing; derive `reference_spacing`
(still returned, still consumed by `pack_pages`) from a *second*, purely observational
measurement pass over the width-rescaled results, not from control; rewrite
`CLAUDE.md` §d; full test-suite replacement for `tests/test_normalize_scales.py` plus
new end-to-end coverage with deliberately different-width fixtures.

**Out:**
- `--insert`'s own rescale (CLAUDE.md §c) stays staff-spacing-based, unchanged. This
  is a known inconsistency going forward — after this change, the whole-set pieces are
  width-consistent but an inserted piece is matched to `piece_arrays[0]`'s *spacing*,
  which is no longer a controlled quantity. Left as an explicit follow-up, not solved
  here.
- No change to `stack_pieces`, `clean_stray_marks`, `crop_to_content`,
  `compute_uniform_scale`, `pack_pages`, `sparse_page_warnings`, `balance_pages`, or
  any page-count/page-layout logic. All of these operate on whatever size a piece
  ends up at; none of them care why.
- No horizontal-only (non-isotropic) stretching. Every rescale in this codebase is
  isotropic (same factor both axes) so note shapes are never distorted — this spec
  does not change that convention.

## Non-negotiable constraints

- **Isotropic rescale only.** Matching the existing codebase convention (and the
  "never alter musical content" rule in CLAUDE.md's Conventions section) — width
  correction is achieved by scaling both axes together, never by stretching one axis.
- **Determinism holds.** Same inputs → same rescale factors → same output bytes.
  `SCALE_NOOP_TOLERANCE`'s identity-preservation behavior (a piece already within
  tolerance of the reference returns the *same object*, unresampled) carries over
  unchanged, just keyed on width instead of spacing.
- **`normalize_piece_scales`'s external interface is unchanged.**
  `(pieces: list[np.ndarray]) -> (list[np.ndarray], list[str], float | None)`, same
  call site in `assemble()`, same position in the pipeline (before the `--insert`
  splice, before `pack_pages`). `pack_pages` and everything downstream needs zero
  code changes.
- **The two parts stay separate.** All logic lives in `scripts/assemble_sheet_music.py`.

## Where it plugs in

Pipeline position is unchanged from today:

```
discover → load → strip_edge_border_lines
        → normalize_piece_scales   ← THIS SPEC changes what happens inside
        → [--insert splice]        (unchanged: still spacing-based)
        → pack_pages(piece_heights, reference_spacing)   (unchanged)
        → _split_into_pages → per page: stack → clean_stray_marks → crop
        → compute_uniform_scale → render_letter_page
```

## The algorithm

### 1. Measure content width

New function, directly parallel to the existing `measure_staff_spacing`:

```python
def measure_content_width(rgb: np.ndarray) -> int:
    """Pixel width of the tightest bounding box around non-background ink,
    the same ink-mask technique crop_to_content uses for its vertical
    extent. Raises ValueError if the piece has no detectable ink (blank)."""
```

Uses the existing `_to_ink_mask` helper (dark-pixel-or-colored-logo detection,
already used by `crop_to_content` and `clean_stray_marks`) and takes
`cols[-1] - cols[0] + 1` where `cols = np.flatnonzero(mask.any(axis=0))`. Raises
`ValueError` when `cols` is empty (a fully blank piece), mirroring
`measure_staff_spacing`'s "can't measure this" contract so the existing
"unmeasurable piece" handling pattern in `normalize_piece_scales` needs no new
branch shape — just a different measurement function underneath it.

Runs on the piece **after** `strip_edge_border_lines` (same pipeline position
`measure_staff_spacing` currently runs at), so a leftover screenshot border strip
doesn't inflate the measured width.

No special-casing for title-block text (page 1 of a real set often has a title/composer
credit block whose horizontal extent may not match the staff below it) — the existing
median-based robustness (one or two outliers can't drag the reference) is the same
tool the codebase already relies on for staff-spacing outliers, and is expected to
cover this the same way.

### 2. Rescale to the median

`normalize_piece_scales` keeps its existing structure, swapping the measurement call:

- For each piece, `measure_content_width` (catch `ValueError` → unmeasurable, same
  warning pattern as today: `"piece {i}: content width unmeasurable; left at native
  scale"`).
- If fewer than 2 pieces are measurable, return all pieces unchanged (no reliable
  reference) — same early-return shape as today.
- Reference = median of measurable widths.
- For each measurable piece, `factor = rescale_factor(reference, width)`; if
  `abs(factor - 1.0) <= SCALE_NOOP_TOLERANCE`, keep the same object (no resample);
  otherwise `resize_rgb(piece, factor)` and warn
  `"piece {i}: rescaled x{factor:.3f} to match staff width"`.

This is a mechanical swap of the existing loop's measured quantity — no new control
flow shape.

### 3. Derive `reference_spacing` for `pack_pages` (observational, not controlling)

After the width-rescale pass produces the final piece list, run a **second**
measurement pass — `measure_staff_spacing` (unchanged, existing function) — over
those *already-rescaled* pieces:

- Collect `measure_staff_spacing` results, skipping unmeasurable pieces (same
  `ValueError` catch pattern).
- If fewer than 2 pieces yield a measurable spacing, `reference_spacing = None`
  (`pack_pages` already handles `None` by delegating to `balance_pages` — no change
  needed there).
- Otherwise `reference_spacing = float(np.median(measured_spacings))`.

This value is no longer "what every piece was forced to be" — it's "a representative
sample of this run's note size after width-normalization," which is exactly what
`pack_pages`'s height-budget model (`h_ref = REF_SNIPPET_HEIGHT_SPACINGS *
reference_spacing`) needs. `pack_pages` and `sparse_page_warnings` require zero code
changes.

## Interface changes

1. **New `measure_content_width(rgb: np.ndarray) -> int`** — as above.
2. **`normalize_piece_scales`** — same signature, same warnings-list shape (still
   `list[str]`), internals restructured into the two passes above. Warning message
   text changes (`"content width"` / `"to match staff width"` instead of `"staff
   spacing"` / `"to match staff spacing"`) — no code outside this function parses
   warning text, so this is a safe wording change.
3. **No changes** to `measure_staff_spacing`, `rescale_factor`, `resize_rgb`,
   `pack_pages`, `sparse_page_warnings`, `_page_height_budget`, `_group_height`,
   `balance_pages`, or any `assemble()` call site other than the one line that already
   calls `normalize_piece_scales` (unchanged call, unchanged unpacking).

## Edge cases

- **Blank/no-ink piece.** `measure_content_width` raises `ValueError`; left at native
  scale, warned, same as today's unmeasurable-spacing case.
- **Fewer than 2 measurable widths.** No reliable reference; all pieces returned
  unchanged (same shape as today's "fewer than 2 measurable" branch).
- **Fewer than 2 measurable *spacings* after width-rescale.** `reference_spacing =
  None`; `pack_pages` falls back to `balance_pages` for that run (existing, tested
  behavior — no new code path).
- **Title-block outlier (piece 1 of a real set).** Not special-cased; relies on
  median robustness, same as any other outlier this codebase already tolerates.
- **Already-uniform-width set.** Every piece within `SCALE_NOOP_TOLERANCE` → every
  piece returned as the same object → byte-identical output, same determinism
  guarantee as today.

## Testing

TDD, synthetic fixtures. **Full replacement** of `tests/test_normalize_scales.py`
(today's 6 tests are entirely about spacing being the controlled variable, so they
test the behavior this spec removes, not the behavior it adds):

- **Mismatched widths converge:** two pieces with different content widths (padding
  differs, not spacing) end up at matching width after normalization.
- **Blank piece left alone:** a piece with no ink is returned unchanged (identity)
  while others still normalize to each other; a warning names it.
- **Already-consistent set is unchanged:** identity check, same as today's pattern.
- **Fewer than 2 measurable widths is a no-op:** same pattern as today.
- **`reference_spacing` is the median of *post-rescale* observed spacing:** a new
  test — build pieces with different widths AND different (but each individually
  measurable) staff spacing, verify the returned `reference_spacing` matches the
  median spacing measured on the width-rescaled outputs, not on the raw inputs.
- **`reference_spacing` is `None` when fewer than 2 rescaled pieces have measurable
  spacing:** new test mirroring the width-side "fewer than 2" case but for the
  spacing-observation pass.

New end-to-end test in `tests/test_cli_end_to_end.py`: a set of synthetic snippets
built with **deliberately different content widths** (today's `_snippet()` fixture
always uses the same `width=800` for every piece in a test, so this exact bug is
currently untestable end-to-end) — assemble them and assert every page's stacked
systems end up the same width post-normalization.

## Contract edits (`CLAUDE.md` §d)

Rewrite "Whole-set staff-spacing normalization" (currently describing spacing-based
control) to describe width-based control: measure content width via the same
ink-mask technique as `crop_to_content`'s vertical extent; rescale to median width;
`SCALE_NOOP_TOLERANCE` and identity-preservation carry over unchanged; note that
`reference_spacing` (used by the height-aware page packer, §b) is now a derived
*observation* of post-rescale spacing, not a controlled quantity — call out the one
known inconsistency this creates with `--insert` (§c), which still matches spacing,
flagged as a follow-up rather than solved here.

## Open questions for the implementation plan

- Exact wording of the new warning messages (`"content width"` phrasing above is a
  starting point, not locked).
- Whether the new end-to-end differing-width test needs more than 2 distinct widths
  to be a meaningful regression (leaning: 2 is enough, mirrors the existing spacing
  end-to-end tests' style).
