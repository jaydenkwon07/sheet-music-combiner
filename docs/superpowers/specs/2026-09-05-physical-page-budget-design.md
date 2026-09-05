# Sheet Music Assembler — Physical Page-Height Budget

**Date:** 2026-09-05
**Status:** Approved (design), pending implementation plan
**Type:** Algorithm change to the deterministic CLI (`scripts/assemble_sheet_music.py`), no AI

## Purpose

The height-aware page packer (`pack_pages`) currently sizes its per-page budget from
a **staff-spacing proxy**: `B = 6 * (12 * reference_spacing) + 5 * gap`, i.e. "six
normal single-staff snippets worth of height." That proxy models a single-staff
snippet, so on real multi-staff piano+vocal systems (~28-30 staff-spacings tall, not
12) it under-counts page capacity by 2×+ and fragments the output. Confirmed on a real
11-piece Korean worship lead sheet: it produces **5 pages, each ~half empty**, where
the content fits comfortably in **4** at the same notation size (verified against the
current code).

Replace the spacing proxy with a **physically-grounded budget**: the content-pixel
height that actually fits on a US-Letter page at the run's uniform scale, filled to a
comfortable fraction. This makes page count follow real page geometry instead of a
proxy constant.

## Key insight (why this is well-posed)

After the whole-set width normalization (shipped 2026-09-04), every piece has ~the same
content width `W`. `compute_uniform_scale` picks
`scale = min(usable_w / max_page_w, usable_h / max_page_h)`. Since all pages share width
`≈ W`, the width term `s_w = usable_w / W` is **fixed and independent of how we pack** —
it is a hard ceiling on notation size that no packing can beat.

So the goal reduces to: keep every page short enough that the *height* term never
becomes the binding constraint (which would shrink notation below `s_w`), using the
fewest pages that achieves it. A page stays non-binding as long as its stacked content
height `≤ usable_h / s_w`. Filling to a comfort fraction of that is the budget:

```
s_w = usable_w / W
B   = PAGE_FILL_FRACTION * usable_h / s_w
    = PAGE_FILL_FRACTION * usable_h * W / usable_w
```

with `usable_w = LETTER_WIDTH_PX - 2*margin`, `usable_h = LETTER_HEIGHT_PX - 2*margin`,
and `PAGE_FILL_FRACTION = 0.9` (a 90 %-full target leaves engraving-like breathing room
top and bottom rather than cramming systems to the page edge).

**The budget is only a packing heuristic, not the source of truth for scaling.**
`compute_uniform_scale` still computes the actual uniform scale from the actual rendered
page dimensions after stacking/cropping. So `B` does not have to be pixel-exact — a
small drift between `W` (the pre-stack median width) and the real post-stack page width
is corrected by `compute_uniform_scale`. `B` only needs to be approximately right to
choose good page counts and partitions.

## Scope

**In:** replace `_page_height_budget`'s spacing-proxy formula with the geometry formula
above; change `pack_pages` and `sparse_page_warnings` to take `reference_width` +
`margin` instead of `reference_spacing`; change `num_pages` selection to
smallest-`k`-that-fits-the-budget; change `normalize_piece_scales` to return
`reference_width` (the median it already computes) instead of `reference_spacing`, and
**remove its now-dead observational staff-spacing second pass**; delete
`REF_SNIPPET_HEIGHT_SPACINGS`; add `PAGE_FILL_FRACTION`; rewrite `CLAUDE.md` §b and the
relevant bullet of §d; rewrite `tests/test_pack_pages.py`; update
`tests/test_normalize_scales.py` and `tests/test_cli_end_to_end.py`.

**Out:**
- `compute_uniform_scale` — untouched. It remains the source of truth for actual
  scaling; this change only feeds it better page groupings.
- The two-stage min-max / sum-of-squares partition DP inside `pack_pages` — its logic
  is unchanged; only how `num_pages` is chosen and how the budget is computed change.
- `--insert`'s spacing-based rescale (§c), `stack_pieces`, `clean_stray_marks`,
  `crop_to_content`, `render_letter_page`, `balance_pages`, `measure_staff_spacing`,
  `measure_content_width`, `rescale_factor`, output naming, the PDF path, and the
  web/subagent layers.
- `rescale_factor` keeps its parameter name `reference_spacing` — it is a generic
  `reference / inserted` ratio used by `--insert` and by width normalization alike, and
  is referenced by keyword in `tests/test_staff_spacing.py`. Not renamed.

## Non-negotiable constraints

- **Determinism holds.** Same inputs → same budget → same partition → same output
  bytes. The budget is pure integer/float arithmetic over module constants and the
  measured median width; the partition DP is already deterministic.
- **The two parts stay separate.** All logic lives in
  `scripts/assemble_sheet_music.py`.
- **Notation size is never reduced below the width-bound ceiling by the packer.** The
  budget exists precisely to keep the height term non-binding at ~90 % fill;
  `compute_uniform_scale` still honestly computes the final scale, so if a single
  system is genuinely taller than a page allows, it still shrinks (unavoidable) — but
  the packer never *chooses* a taller-than-necessary page.
- **No per-page count cap.** Pages fill purely by measured height (explicit design
  decision). The old 4-6-per-page count model and its regression examples
  (N=11→[6,5], etc.) are intentionally retired.

## Where it plugs in

Pipeline position is unchanged:

```
discover → load → strip_edge_border_lines
        → normalize_piece_scales   (now returns reference_WIDTH; observational
                                     spacing pass removed)
        → [--insert splice]
        → pack_pages(piece_heights, reference_width, margin=margin)  ← THIS SPEC
        → _split_into_pages → per page: stack → clean_stray_marks → crop
        → compute_uniform_scale (unchanged; still the real scale) → render_letter_page
```

## The algorithm

### `num_pages`: smallest k that fits the budget

Replace the current closed-form `ceil((sum + n*gap)/(budget+gap))` with a direct
search: the smallest `k` in `1..N` whose optimal min-max partition height (stage-1 DP)
is `≤ B`. If no `k ≤ N` fits (a single system alone is taller than `B`), clamp to `N`
(one piece per page; that page exceeds 90 % and `compute_uniform_scale` will shrink the
run — unavoidable and rare).

This is robustly correct for the discrete problem (the closed-form can under- or
over-count when pieces are chunky) and directly expresses "fewest pages where every
page is ≤ 90 % full." `N` is small (tens), so iterating `k` and running the O(N²·k) DP
per `k` is cheap.

Once `k` is chosen, the existing two-stage partition (stage 1: minimal tallest-page
height for that `k`; stage 2: among ties, minimize sum of squared page heights;
front-loaded final tie-break) is reused **unchanged** to produce the counts.

### Budget

```python
PAGE_FILL_FRACTION = 0.9

def _page_height_budget(reference_width, margin=DEFAULT_MARGIN_PX):
    usable_w = LETTER_WIDTH_PX - 2 * margin
    usable_h = LETTER_HEIGHT_PX - 2 * margin
    return PAGE_FILL_FRACTION * usable_h * reference_width / usable_w
```

### `normalize_piece_scales` simplification

It already computes `reference_width = float(np.median(measured_widths))` for the
rescale. Return that as the third value instead of `reference_spacing`, and **delete the
observational second pass** (the loop that re-measures `measure_staff_spacing` on the
rescaled pieces) — it existed solely to feed the old spacing-proxy budget and has no
remaining consumer. When fewer than 2 pieces have measurable width, return `None` for
`reference_width` (same fallback shape as today), and `pack_pages(None)` delegates to
`balance_pages`.

## Verified behavior (computed against current code, not hand-derived)

- **Real 11-piece file** (`W=1461`, `B≈1708` content px): **4 pages** (was 5).
- **Realistic single-staff set** (width 1450, height 360, ~4:1 aspect, `B≈1696`):
  N=6→2 pages, N=7→2, N=11→3, N=14→4, N=15→4. These are denser or sparser than the old
  fixed 4-6 depending on real aspect — and always keep notation at the width-bound size.
  Note N=6→2 (not 1): six 360-px systems stacked (~2360 px) exceed even the 100 %
  capacity (~1884 px) at that width, so one page can't hold them at full scale — the old
  `[6]` silently forced height-binding and shrank the notation. The new behavior is the
  physically honest one.

## Edge cases

- **No reliable reference width** (fewer than 2 measurable pieces): `reference_width`
  is `None`; `pack_pages` delegates to `balance_pages(n)` unchanged (including its N=7
  `PageBalanceError`). Preserves today's fallback.
- **A single system taller than `B`** (or taller than the 100 % page capacity): iterate
  finds no fitting `k`, clamp to `N`; that page renders larger and `compute_uniform_scale`
  shrinks the whole run. No crash.
- **`--pages` override**: unchanged; skips `pack_pages` entirely.
- **`--at-position` pre-insert mapping**: still calls `pack_pages`; updated to pass
  `reference_width` + `margin` (computed the same way as the main call).
- **Sparse page**: `sparse_page_warnings` keeps warning when a page falls under 40 % of
  the (new) budget — a tall system forced an uneven split. Same channel, same threshold
  fraction, new budget basis.

## Interface changes

1. **`normalize_piece_scales(pieces) -> (list[np.ndarray], list[str], float | None)`** —
   signature shape unchanged; third value is now the median **content width** (or
   `None`), and the observational staff-spacing pass is removed.
2. **`pack_pages(piece_heights, reference_width, *, margin=DEFAULT_MARGIN_PX, gap=STACK_GAP_PX) -> list[int]`**
   — `reference_spacing` param becomes `reference_width`; add `margin`.
3. **`sparse_page_warnings(piece_heights, counts, reference_width, *, margin=DEFAULT_MARGIN_PX, gap=STACK_GAP_PX) -> list[str]`**
   — same substitution.
4. **`_page_height_budget(reference_width, margin=DEFAULT_MARGIN_PX)`** — geometry
   formula above.
5. **Constants:** add `PAGE_FILL_FRACTION = 0.9`; delete `REF_SNIPPET_HEIGHT_SPACINGS`.
   `MAX_PER_PAGE`/`MIN_PER_PAGE` remain only for the `balance_pages` fallback.
6. **`assemble()` call sites** (main pack + `--at-position` pre-layout): unpack
   `reference_width`, pass it and `margin` to `pack_pages`/`sparse_page_warnings`.

## Testing

TDD. Because the geometry budget's page counts depend on snippet **aspect ratio**,
tests must use realistic aspect fixtures (wide staff, e.g. width ~1450, height a few
hundred) — the existing `_snippet` fixtures with unrealistic aspect (e.g. 800×360,
200×82) would pack in aspect-artifact ways. Prefer **property assertions** over
hard-coded distributions where the exact split isn't the point:

- **Budget geometry:** `_page_height_budget(W)` equals
  `0.9 * usable_h * W / usable_w` for a couple of `W`/margin values.
- **Fewest-pages-that-fit:** for a realistic set, assert the returned `k` is the
  smallest `k` whose min-max partition height ≤ `B` (recompute independently in the
  test), and that every page's stacked height ≤ `B` when achievable.
- **Denser tall input uses fewer pages than the old proxy would:** a set of tall
  (multi-staff-height) uniform pieces packs to materially fewer pages than
  `ceil(total/oldproxy)` would have — pin the real-file-like case (11 pieces of the
  measured heights → 4 pages).
- **Single-system-taller-than-page → one per page, no crash** (clamp to N).
- **No-reference-width fallback:** `pack_pages(heights, None)` equals
  `balance_pages(len(heights))`.
- **`--pages` override still wins.**
- **Determinism:** identical inputs → identical partition and identical output bytes
  (the existing end-to-end determinism test continues to cover this; update its fixture
  aspect if needed).
- **Sparse warning:** a genuinely lopsided split (one very tall system) still emits the
  warning under the new budget.
- **`normalize_piece_scales`:** update the two `reference_spacing`-observation tests to
  assert `reference_width` (median content width) and its `None` case; the width-rescale
  and clamp tests are unaffected.
- **End-to-end:** update `tests/test_cli_end_to_end.py` fixtures/assertions that assumed
  the old count model (the N=7 auto-resolve and sparse tests reference `pack_pages`
  spacing behavior in comments/shape) to the geometry model.

`tests/test_pack_pages.py` is effectively rewritten (it is built entirely around the
spacing-proxy budget and the retired 4-6 count examples).

## Contract edits

- **`CLAUDE.md` §b (Page-count balancing):** replace the spacing-budget description with
  the geometry budget (`B = 0.9 * usable_h * W / usable_w`, width-bound-scale reasoning,
  smallest-k-that-fits, no per-page cap, `--pages`/fallback unchanged). Remove the
  `REF_SNIPPET_HEIGHT_SPACINGS` and 4-6 verified-examples language; state that page
  count now follows physical page fit at ~90 %.
- **`CLAUDE.md` §d:** update the bullet that says `reference_spacing` is returned for the
  packer — it now returns `reference_width`, and the observational spacing pass is gone.

## Open questions for the implementation plan

- Exact `PAGE_FILL_FRACTION` (start 0.9; the only tunable — adjust only if a realistic
  fixture looks cramped or too sparse).
- Whether to keep `sparse_page_warnings`'s 40 % threshold or express it relative to the
  new budget's fill target (leaning: keep 40 % of `B` — it still means "markedly emptier
  than intended").
