# Sheet Music Assembler — Height-Based Page Packing

**Date:** 2026-09-03
**Status:** Approved (design), pending implementation plan
**Type:** Layout algorithm change to the deterministic CLI (`scripts/assemble_sheet_music.py`), no AI

## Purpose

Make the number of snippets per page adapt to snippet **size** instead of a
fixed 4–6 count band. A snippet of a voice line plus piano accompaniment is
~2–2.5× the height of a single piano staff; packing 5–6 of those onto a page
forces the uniform scale down and makes every page's notation small. Tall
snippets should land ~3–4 per page, normal staves ~5–6, and mixed sets should
sort themselves out — all decided by measured proportions, deterministically.

This is the first of two independent improvements agreed for
`sheet-music-combiner` (the second, flexible filename ingestion, is a separate
later spec). The earlier-identified "rescale mismatched snippet sizes" problem
is already solved by `normalize_piece_scales` in the working tree and is not
part of this spec — it is a **prerequisite** (see Dependencies).

## Scope

**In:** replace `balance_pages(n: int) -> list[int]` with a height-aware
packer; expose the run's reference staff spacing from `normalize_piece_scales`;
update the `--at-position` pre-insert layout mapping to use the new packer;
rewrite `CLAUDE.md` §b; regression + new tests.

**Out:** any change to `stack_pieces`, `clean_stray_marks`, `crop_to_content`,
`compute_uniform_scale`, `render_letter_page`, output naming, the PDF path, or
the web/subagent layers. `--pages` override behaviour is unchanged. No LLM
calls — the "No LLM calls, no guessing" contract line stands.

## Non-negotiable constraints

- **Determinism holds.** Same inputs → same partition → same output bytes. The
  packer uses only measured pixel heights and integer arithmetic; ties break by
  a fixed rule (front-loading, below).
- **The two parts stay separate.** All logic lives in
  `scripts/assemble_sheet_music.py`. The bridge and subagent are untouched.
- **Backward compatible for the common case.** A set of uniform single-staff
  snippets must still produce today's splits (see Verified examples).

## Where it plugs in

`assemble()` pipeline position is unchanged. The packer runs immediately after
`normalize_piece_scales` (and after the `--insert` splice), replacing the
`balance_pages(total)` call. Everything downstream already consumes a
`list[int]` of per-page counts and is untouched.

```
discover → load → strip_edge_border_lines
        → normalize_piece_scales      (prerequisite; now also returns reference spacing s)
        → [--insert splice]
        → pack_pages(piece_heights, s) ← THIS SPEC  (was: balance_pages(total))
        → _split_into_pages → per page: stack → clean_stray_marks → crop
        → compute_uniform_scale (one scale, all pages) → render_letter_page
```

## The height model

All quantities are in "normalized pixels" — the coordinate space after
`normalize_piece_scales` has rescaled every measurable piece to the run's
median staff-line spacing `s`.

| Symbol | Definition |
|---|---|
| `s` | run's median staff-line spacing, in px, from `normalize_piece_scales` |
| `REF_SNIPPET_HEIGHT_SPACINGS` | **calibration constant ≈ 12.0** — a normal single-system snippet is ~12× `s` tall (4-gap staff + typical note / stem / ledger / lyric margin). Tuned against fixtures. |
| `h_ref` | `REF_SNIPPET_HEIGHT_SPACINGS * s` — reference height of one "normal" snippet |
| `STACK_GAP_PX` | existing constant (40), vertical gap `stack_pieces` inserts between snippets |
| `B` | page height budget = `6 * h_ref + 5 * STACK_GAP_PX`. The `6` is `MAX_PER_PAGE`, kept as the anchor that reproduces today's behaviour. |
| `h[i]` | measured pixel height of piece `i` (its array's `shape[0]`), post-normalization, pre-crop |
| `total_height` | `sum(h) + (N - 1) * STACK_GAP_PX` |
| `num_pages` | `clamp(ceil(total_height / B), 1, N)` |

`B` is an **absolute** budget (fixed multiple of `s`), not a multiple of this
run's median piece height — that is what lets tall snippets produce more pages.

- Uniform normal staves: `h[i] ≈ h_ref` → `num_pages ≈ ceil(N / 6)`.
- Voice + piano (`h[i] ≈ 2.5·h_ref`): `num_pages ≈ ceil(2.5·N / 6) ≈ ceil(N / 2.4)`.

## The partition

Given `num_pages = k`, split the ordered piece list into exactly `k`
**contiguous** groups (order is fixed — snippets are a sequence, never
reordered) minimising the tallest group's `total_height`. Classic
linear-partition dynamic programming, `O(N²·k)`; `N` is small (tens at most).

**Group height** for a candidate group of pieces `a..b` is
`sum(h[a..b]) + (b - a) * STACK_GAP_PX`.

**Tie-break (determinism + parity with today's front-loading):** among all
partitions achieving the optimal min-max height, choose the one whose earlier
pages are fuller. Concretely: reconstruct the DP taking, at each step, the
**largest** first-group size that still allows the remainder to be partitioned
optimally. This yields `[6,5]` not `[5,6]`, `[5,5,4]` not `[4,5,5]`.

### Verified examples (regression — must match exactly, uniform `h`)

| N | k | result |
|---|---|---|
| 11 | 2 | `[6, 5]` |
| 15 | 3 | `[5, 5, 5]` |
| 14 | 3 | `[5, 5, 4]` |
| 7 | 2 | `[4, 3]` |

N=7 previously had no valid split under the rigid `MIN_PER_PAGE = 4` count
floor and printed two options for the caller to choose. That floor is removed
(below), so N=7 resolves to `[4, 3]` automatically — **no prompt**.

## Edge cases

- **No reliable reference.** If `normalize_piece_scales` found fewer than 2
  measurable pieces it returns no `s`. The packer then falls back to today's
  count-based `balance_pages(N)` unchanged — no behaviour change for inputs
  with no detectable staff lines.
- **Sparse page.** After partitioning, if any page's group height is
  `< 0.4 * B` and `num_pages > 1`, append a warning to the summary list (same
  channel as stray-mark notices, e.g. `"page 4: sparse (1 piece); a very tall
  snippet elsewhere forced an uneven split"`). Never blocks. This replaces the
  old `MIN_PER_PAGE` hard error.
- **`num_pages` clamped to N.** Every snippet taller than `B` →
  `ceil(total_height / B) > N` → clamp to `N`, one piece per page. The uniform
  scale shrinks them to fit; no crash, no special-casing.
- **`--pages "5,5,4"` override.** Skips the packer entirely;
  `parse_pages_override` is unchanged and still authoritative (may fall outside
  any band).
- **`--insert --at-position <page>:<index>`.** The pre-insert position→flat-index
  mapping must use the new packer on the pre-insert piece heights, so
  `<page>:<index>` refers to the layout the caller is actually looking at.
  `flat_index_for_position` keeps its signature (`pages: list[int]`); the caller
  computes those counts via the new packer instead of `balance_pages`.

## Interface changes

1. **`normalize_piece_scales(pieces) -> (pieces, warnings, reference_spacing)`**
   — add a third return value: the `float` median spacing it already computes,
   or `None` when it bailed (fewer than 2 measurable). Update its one call site
   in `assemble()` and its tests.

2. **New `pack_pages(piece_heights: list[int], reference_spacing: float | None,
   *, gap: int = STACK_GAP_PX) -> list[int]`** — the height-aware packer.
   `reference_spacing is None` → delegates to `balance_pages(len(piece_heights))`.
   Returns per-page counts summing to `len(piece_heights)`.

3. **`balance_pages(n)`** — retained (used as the no-reference fallback and by
   nothing else). Its `PageBalanceError` / N=7 branch becomes dead code for the
   normal path but stays for the fallback; the `MIN_PER_PAGE` check inside it is
   left as-is for that fallback only. *(Implementation note: confirm during the
   plan whether the fallback should also drop the N=7 raise — leaning yes, for
   consistency, but it is out of the measured path.)*

4. **Constants:** add `REF_SNIPPET_HEIGHT_SPACINGS = 12.0`. Keep
   `MAX_PER_PAGE = 6` (now the budget anchor). `MIN_PER_PAGE` stays only for the
   `balance_pages` fallback.

## Dependencies & sequencing

`normalize_piece_scales` and `tests/test_normalize_scales.py` are currently
**uncommitted** in the working tree (4 tests passing). They must be committed
first — the packer needs both the normalized heights and the reference spacing.
Implementation order:

1. Commit the `normalize_piece_scales` WIP as-is.
2. Add the third return value (`reference_spacing`) to it.
3. Build `pack_pages` + tests.
4. Wire `pack_pages` into `assemble()`, including the `--at-position` mapping.
5. Rewrite `CLAUDE.md` §b.

## Testing

TDD, synthetic fixtures (white images with black full-width rows at a known
spacing, sized to a target snippet height).

- **Regression:** uniform-height pieces reproduce `[6,5]` / `[5,5,5]` /
  `[5,5,4]`; N=7 → `[4,3]`.
- **Tall snippets:** 12 pieces at ~2.5× `h_ref` → ~3 per page; assert each
  page's height ≤ `B` where achievable and pages are balanced.
- **Mixed:** 3 tall + 6 short, order preserved, no crash, min-max height
  optimal.
- **Sparse warning:** 1 piece at ~5× `h_ref` + 5 tiny pieces → a
  `sparse` warning is emitted; run still completes.
- **Clamp:** 5 pieces each `> B` → `[1,1,1,1,1]`, no crash.
- **No-reference fallback:** a piece list whose `reference_spacing` is `None`
  → identical result to `balance_pages(N)` for the same N.
- **`--pages` override** still wins and is unaffected.
- **Determinism:** identical inputs run twice → identical partition and
  identical output PNG/PDF bytes.
- **Insert:** `--at-position` on a set with tall snippets lands the inserted
  piece at the position the new layout shows.

## Contract edits (`CLAUDE.md` §b)

Rewrite "Page-count balancing" to describe:

- Height budget `B` derived from `s` and `REF_SNIPPET_HEIGHT_SPACINGS`;
  `num_pages = clamp(ceil(total_height / B), 1, N)`.
- Contiguous linear-partition DP minimising the tallest page, front-loaded
  tie-break.
- The four verified examples above, framed as "uniform single-staff inputs
  still produce these."
- N=7 → `[4,3]` automatically; **remove** the "print two options / ask the
  caller" paragraph and the `MIN_PER_PAGE` gap-case language.
- `--pages` override unchanged; add that a too-sparse page now emits a warning
  rather than failing.

## Open questions for the implementation plan

- Exact value of `REF_SNIPPET_HEIGHT_SPACINGS` — start at `12.0`, adjust only
  if a regression fixture built from realistic snippet proportions misses a
  verified example.
- Whether the `balance_pages` fallback should also drop its N=7 raise
  (leaning yes).
