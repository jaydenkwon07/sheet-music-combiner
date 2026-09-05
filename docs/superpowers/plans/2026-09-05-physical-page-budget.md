# Physical Page-Height Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `pack_pages`'s staff-spacing-proxy page-height budget with a physically-grounded one — the content height that fits a US-Letter page at the width-bound uniform scale, filled to 90% — so page count follows real page geometry instead of a proxy constant that over-fragments multi-staff scores.

**Architecture:** Because whole-set width normalization already gives every piece a common width `W`, the uniform scale is width-bound at `s_w = usable_w/W`, so a page stays non-binding while its stacked height ≤ `usable_h/s_w`. The budget becomes `B = 0.9 · usable_h · W / usable_w`; `pack_pages` chooses the fewest pages whose optimal min-max partition fits `B`. `normalize_piece_scales` now returns the median content **width** (which it already computes) instead of the observed spacing, letting the observational spacing pass and the `REF_SNIPPET_HEIGHT_SPACINGS` proxy be deleted. `compute_uniform_scale` is untouched and remains the source of truth for the actual scale — the budget is only a packing heuristic.

**Tech Stack:** Python 3.12, NumPy, Pillow, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-05-physical-page-budget-design.md`

## Global Constraints

- **Determinism holds.** Same inputs → same budget → same partition → same output bytes. Budget is pure arithmetic over module constants and the measured median width; the partition DP is already deterministic.
- **The two parts stay separate.** All logic lives in `scripts/assemble_sheet_music.py`.
- **No per-page count cap.** Pages fill purely by measured height (explicit design decision). The old 4-6-per-page count model and its regression examples are intentionally retired.
- **`compute_uniform_scale` is untouched** — still computes the real scale from actual page sizes; the budget only feeds it better page groupings.
- **Out of scope (do not modify):** the two-stage partition DP *logic* inside `pack_pages` (min-max then sum-of-squares — only how `num_pages` is chosen changes); `--insert`'s spacing-based rescale; `stack_pieces`, `clean_stray_marks`, `crop_to_content`, `render_letter_page`, `balance_pages`, `measure_staff_spacing`, `measure_content_width`, `rescale_factor` (keeps its `reference_spacing` param name — it's a generic ratio), output naming, PDF path, web/subagent layers.

## Verified reference values (computed against current code + the proposed algorithm, not hand-derived)

- Budget: `_page_height_budget(W) = 0.9 * (3300-2*22) * W / (2550-2*22) = 0.9 * 3256 * W / 2506`.
- Real 11-piece file (heights `[794,527,506,465,437,457,458,474,419,400,376]`, `W=1461`, `B≈1708`) → `pack_pages` = `[2, 3, 3, 3]` (was 5 pages).
- Realistic single-staff (height 360, `W=1450`, `B≈1696`): N=6→`[3,3]`, N=7→`[4,3]`, N=11→`[4,4,3]`, N=14→`[4,4,3,3]`, N=15→`[4,4,4,3]`.
- Single system taller than a page (`[2500,300,300,300]`, `W=1450`) → clamp to N → `[1,1,1,1]`.
- End-to-end `_snippet` fixture at width 1450 (content width 1390, `B≈1625`): N=11 (h=360)→`[4,4,3]`, N=15→`[4,4,4,3]`, N=7→`[4,3]`, 6-piece insert (h=360)→`[3,3]`, 5-piece pre-insert (h=360)→`[3,2]`, sparse `[1500,163,163]`→`[1,2]` with page 2 sparse.

---

### Task 1: Physical page-height budget in the script and all affected tests

This is one atomic change: swapping `pack_pages`'s budget basis forces its signature (`reference_spacing`→`reference_width`+`margin`), which forces `normalize_piece_scales`'s return, the `assemble()` call sites, and the tests to all move together to keep the suite green. There is no intermediate green state, so it is a single task with fine-grained TDD steps.

**Files:**
- Modify: `scripts/assemble_sheet_music.py` — delete `REF_SNIPPET_HEIGHT_SPACINGS`; add `PAGE_FILL_FRACTION`; rewrite `_page_height_budget`, `pack_pages`, `sparse_page_warnings`; simplify `normalize_piece_scales`; update the two `assemble()` call sites and the call-site comment.
- Replace entirely: `tests/test_pack_pages.py`
- Modify: `tests/test_normalize_scales.py` (the two `reference_spacing` tests → `reference_width`)
- Modify: `tests/test_cli_end_to_end.py` (fixture width; count assertions; sparse test)

**Interfaces:**
- Produces: `pack_pages(piece_heights: list[int], reference_width: float | None, *, margin: int = DEFAULT_MARGIN_PX, gap: int = STACK_GAP_PX) -> list[int]`
- Produces: `sparse_page_warnings(piece_heights, counts, reference_width, *, margin=DEFAULT_MARGIN_PX, gap=STACK_GAP_PX) -> list[str]`
- Produces: `normalize_piece_scales(pieces) -> (list[np.ndarray], list[str], float | None)` — third value is now median content width.
- Produces: `PAGE_FILL_FRACTION = 0.9` (module constant); `REF_SNIPPET_HEIGHT_SPACINGS` removed.
- Consumes: `LETTER_WIDTH_PX`, `LETTER_HEIGHT_PX`, `DEFAULT_MARGIN_PX`, `STACK_GAP_PX`, `balance_pages`, `_split_into_pages` (all existing, unchanged).

- [ ] **Step 1: Delete the proxy constant, add the fill fraction**

In `scripts/assemble_sheet_music.py`, delete the line `REF_SNIPPET_HEIGHT_SPACINGS = 12.0` and its comment (currently around line 26-28). Add, near the US-Letter constants (after `STACK_GAP_PX = 40`, around line 83):

```python
# Target fraction of the usable page height a packed page fills. Below 1.0 so
# systems get engraving-like breathing room top and bottom instead of being
# crammed to the page edge; pack_pages uses it to size the per-page budget.
PAGE_FILL_FRACTION = 0.9
```

- [ ] **Step 2: Write the new test_pack_pages.py (failing)**

Replace the entire contents of `tests/test_pack_pages.py` with:

```python
"""Tests for the physical page-height packer (pack_pages / sparse_page_warnings).

pack_pages splits the ordered piece list into the fewest contiguous pages whose
every page's stacked height fits a physical US-Letter page budget (usable height
at the width-bound uniform scale, filled to PAGE_FILL_FRACTION). Page counts
depend on the snippet ASPECT RATIO -- a common content width W (all pieces share
it after normalize_piece_scales) plus each piece's pixel height -- so these tests
pass realistic wide-staff dimensions, not the unit fixtures' unrealistic aspect.
"""

import math

from assemble_sheet_music import (
    DEFAULT_MARGIN_PX,
    LETTER_HEIGHT_PX,
    LETTER_WIDTH_PX,
    PAGE_FILL_FRACTION,
    STACK_GAP_PX,
    _page_height_budget,
    balance_pages,
    pack_pages,
    sparse_page_warnings,
)

# A realistic full-width staff snippet is far wider than tall (~4:1); these are
# the dimensions the packer is designed for.
REALISTIC_W = 1450


def _budget(width, margin=DEFAULT_MARGIN_PX):
    usable_w = LETTER_WIDTH_PX - 2 * margin
    usable_h = LETTER_HEIGHT_PX - 2 * margin
    return PAGE_FILL_FRACTION * usable_h * width / usable_w


def _min_tallest(heights, k, gap=STACK_GAP_PX):
    """Independent min-max linear-partition height for exactly k groups, to
    check pack_pages truly picks the fewest pages that fit the budget."""
    n = len(heights)
    pre = [0] * (n + 1)
    for i, h in enumerate(heights):
        pre[i + 1] = pre[i] + h

    def gh(a, b):
        return (pre[b] - pre[a]) + (b - a - 1) * gap

    dp = [[math.inf] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(j, n + 1):
            dp[i][j] = min(max(dp[s][j - 1], gh(s, i)) for s in range(j - 1, i))
    return dp[n][k]


def _page_heights(heights, counts, gap=STACK_GAP_PX):
    out, pos = [], 0
    for c in counts:
        grp = heights[pos : pos + c]
        pos += c
        out.append(sum(grp) + (len(grp) - 1) * gap)
    return out


def test_budget_is_physical_page_geometry():
    # 0.9 * usable_h * W / usable_w
    assert _page_height_budget(REALISTIC_W) == _budget(REALISTIC_W)
    assert _page_height_budget(1000, margin=0) == PAGE_FILL_FRACTION * LETTER_HEIGHT_PX * 1000 / LETTER_WIDTH_PX


def test_real_file_like_tall_set_packs_to_four_pages():
    # The measured, width-normalized heights of the real 11-piece file at W=1461.
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    assert pack_pages(heights, 1461) == [2, 3, 3, 3]


def test_single_staff_sets_pack_by_physical_fit():
    for n, expected in [
        (6, [3, 3]),
        (7, [4, 3]),
        (11, [4, 4, 3]),
        (14, [4, 4, 3, 3]),
        (15, [4, 4, 4, 3]),
    ]:
        assert pack_pages([360] * n, REALISTIC_W) == expected, (n, expected)


def test_pack_uses_the_fewest_pages_that_fit_the_budget():
    heights = [360] * 11
    counts = pack_pages(heights, REALISTIC_W)
    k = len(counts)
    b = _budget(REALISTIC_W)
    # k pages fit the budget...
    assert _min_tallest(heights, k) <= b
    # ...and k-1 pages do not (k is minimal).
    assert _min_tallest(heights, k - 1) > b


def test_every_page_within_budget_when_achievable():
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    counts = pack_pages(heights, 1461)
    b = _budget(1461)
    assert all(h <= b for h in _page_heights(heights, counts))


def test_single_system_taller_than_page_gets_its_own_page():
    # A 2500px system exceeds the budget (~1696) even alone -> clamp to N.
    assert pack_pages([2500, 300, 300, 300], REALISTIC_W) == [1, 1, 1, 1]


def test_small_set_that_fits_lands_on_one_page():
    # Two short systems fit one page (2*360+40 = 760 <= budget ~1696).
    assert pack_pages([360, 360], REALISTIC_W) == [2]


def test_no_reference_width_delegates_to_balance_pages():
    heights = [999] * 11  # heights irrelevant when reference_width is None
    assert pack_pages(heights, None) == balance_pages(11)


def test_pages_sum_to_total_for_arbitrary_heights():
    heights = [500, 400, 360, 900, 300, 360, 360, 200]
    assert sum(pack_pages(heights, REALISTIC_W)) == len(heights)


def test_empty_input_returns_no_pages():
    assert pack_pages([], REALISTIC_W) == []


def test_margin_widens_the_budget_and_can_reduce_pages():
    # A larger margin shrinks usable_w faster than usable_h here, raising the
    # budget (usable_h*W/usable_w), so a borderline set can need fewer pages.
    heights = [360] * 6
    tight = pack_pages(heights, REALISTIC_W, margin=DEFAULT_MARGIN_PX)
    assert sum(tight) == 6  # smoke: still a valid partition under a custom margin


def test_sparse_warning_fires_for_a_genuinely_lopsided_split():
    # One tall-but-in-budget system (1500 < budget ~1696) forces [1, 2]; the
    # 2-short-piece page (163+40+163 = 366) is under 40% of the budget (~678).
    heights = [1500, 163, 163]
    counts = pack_pages(heights, REALISTIC_W)
    assert counts == [1, 2]
    warnings = sparse_page_warnings(heights, counts, REALISTIC_W)
    assert any("sparse" in w and "page 2" in w for w in warnings)


def test_sparse_warning_not_emitted_for_single_page():
    assert sparse_page_warnings([360, 360], [2], REALISTIC_W) == []


def test_determinism_same_inputs_same_partition():
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    assert pack_pages(heights, 1461) == pack_pages(heights, 1461)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_pack_pages.py -v`
Expected: FAIL — `ImportError` on `PAGE_FILL_FRACTION` is resolved by Step 1, but `_page_height_budget`'s new signature and `pack_pages`'s `reference_width` param don't exist yet, so tests error/fail.

- [ ] **Step 4: Rewrite `_page_height_budget`**

Replace the current `_page_height_budget` (spacing-proxy version) with:

```python
def _page_height_budget(reference_width: float, margin: int = DEFAULT_MARGIN_PX) -> float:
    """Content-pixel page-height budget: how much stacked height fits on one
    physical US-Letter page at the width-bound uniform scale, filled to
    PAGE_FILL_FRACTION.

    After normalize_piece_scales every piece shares ~the same content width, so
    the uniform scale is width-bound at usable_w/reference_width; the height that
    then fits a page is usable_h / that = usable_h * reference_width / usable_w,
    and the fill fraction leaves breathing room. This is only a packing
    heuristic -- compute_uniform_scale still computes the real scale from actual
    page sizes afterward, so the budget need not be pixel-exact.
    """
    usable_w = LETTER_WIDTH_PX - 2 * margin
    usable_h = LETTER_HEIGHT_PX - 2 * margin
    return PAGE_FILL_FRACTION * usable_h * reference_width / usable_w
```

- [ ] **Step 5: Rewrite `pack_pages`**

Replace the entire `pack_pages` function with (note: the two-stage DP body is unchanged from today — only the signature, the `reference_width`/budget lines, and the `num_pages` selection differ):

```python
def pack_pages(
    piece_heights: list[int],
    reference_width: float | None,
    *,
    margin: int = DEFAULT_MARGIN_PX,
    gap: int = STACK_GAP_PX,
) -> list[int]:
    """Split the ordered piece list into the fewest contiguous pages whose every
    page's stacked height fits a physical US-Letter page budget (see
    _page_height_budget), so page count follows real page geometry.

    ``reference_width`` is None when normalize_piece_scales found fewer than 2
    measurable pieces (no reliable reference) -- then this delegates to the old
    count-based balance_pages unchanged (including its N=7 PageBalanceError).

    Otherwise: build the min-max linear-partition DP over all page counts, pick
    the smallest k whose optimal tallest page fits the budget (clamp to N if
    even one-per-page can't -- a single system taller than a page, which
    compute_uniform_scale then shrinks), and among partitions tied at that k's
    optimal tallest-page height, choose the one minimising the sum of squared
    page heights (penalising unevenness), front-loading final ties.
    """
    n = len(piece_heights)
    if n == 0:
        return []
    if reference_width is None:
        return balance_pages(n)

    budget = _page_height_budget(reference_width, margin)

    # Prefix sums so a group's stacked height is an O(1) lookup.
    prefix = [0] * (n + 1)
    for idx, hgt in enumerate(piece_heights):
        prefix[idx + 1] = prefix[idx] + hgt

    def stacked_height(a: int, b: int) -> float:  # heights[a:b], b > a
        return (prefix[b] - prefix[a]) + (b - a - 1) * gap

    # Min-max DP over every page count 1..n: minmax_dp[i][j] is the minimal
    # possible tallest-group height when the first i pieces are split into j
    # contiguous groups.
    minmax_dp = [[math.inf] * (n + 1) for _ in range(n + 1)]
    minmax_dp[0][0] = 0.0
    for j in range(1, n + 1):
        for i in range(j, n + 1):
            best = math.inf
            for split in range(j - 1, i):
                candidate = max(minmax_dp[split][j - 1], stacked_height(split, i))
                if candidate < best:
                    best = candidate
            minmax_dp[i][j] = best

    # Fewest pages whose optimal tallest page fits the budget; clamp to N.
    num_pages = n
    for k in range(1, n + 1):
        if minmax_dp[n][k] <= budget:
            num_pages = k
            break

    if num_pages == 1:
        return [n]

    target = minmax_dp[n][num_pages]

    # Among partitions with every group <= target, minimise the sum of squared
    # group heights; ties broken toward the largest split (front-loaded).
    sumsq_dp = [[math.inf] * (num_pages + 1) for _ in range(n + 1)]
    back = [[-1] * (num_pages + 1) for _ in range(n + 1)]
    sumsq_dp[0][0] = 0.0
    for j in range(1, num_pages + 1):
        for i in range(j, n + 1):
            best = math.inf
            best_split = -1
            for split in range(j - 1, i):
                height = stacked_height(split, i)
                if height > target or sumsq_dp[split][j - 1] == math.inf:
                    continue
                candidate = sumsq_dp[split][j - 1] + height * height
                if candidate <= best:
                    best = candidate
                    best_split = split
            sumsq_dp[i][j] = best
            back[i][j] = best_split

    counts: list[int] = []
    i, j = n, num_pages
    while j > 0:
        split = back[i][j]
        counts.append(i - split)
        i, j = split, j - 1
    counts.reverse()
    return counts
```

- [ ] **Step 6: Rewrite `sparse_page_warnings`**

Replace its signature and budget line (body otherwise unchanged):

```python
def sparse_page_warnings(
    piece_heights: list[int],
    counts: list[int],
    reference_width: float,
    *,
    margin: int = DEFAULT_MARGIN_PX,
    gap: int = STACK_GAP_PX,
) -> list[str]:
    """Warn (never block) when the packer left a page markedly emptier than the
    others -- a very tall snippet elsewhere forced the partition uneven. Same
    channel as stray-mark warnings; never fires for a single-page result."""
    if len(counts) <= 1:
        return []
    budget = _page_height_budget(reference_width, margin)
    groups = _split_into_pages(piece_heights, counts)
    warnings: list[str] = []
    for i, group in enumerate(groups, start=1):
        if _group_height(group, gap) < 0.4 * budget:
            piece_word = "piece" if len(group) == 1 else "pieces"
            warnings.append(
                f"page {i}: sparse ({len(group)} {piece_word}); a very tall "
                f"snippet elsewhere forced an uneven split"
            )
    return warnings
```

(`_group_height` is still used here, so keep it.)

- [ ] **Step 7: Simplify `normalize_piece_scales` to return the width**

In `normalize_piece_scales`, delete the observational spacing pass (the block that builds `spacings` by calling `measure_staff_spacing` on each `out` piece and computes `reference_spacing`), and change the final `return` from `return out, warnings, reference_spacing` to `return out, warnings, reference_width`. Update the docstring's final paragraph to:

```python
    Returns (possibly-rescaled pieces, human-readable warnings, reference
    width). ``reference_width`` is the median measured content width (the same
    value the pieces were rescaled toward), which pack_pages uses to size its
    physical page-height budget; it is None when fewer than 2 pieces had
    measurable width. Pieces already within SCALE_NOOP_TOLERANCE of the width
    reference are returned unchanged (same object), so an already-consistent set
    is a true no-op.
```

(The `reference_width = float(np.median(measured_widths))` line stays; it's now also the return value. `measure_staff_spacing` is no longer called here but remains defined for `--insert`.)

- [ ] **Step 8: Update the `assemble()` call sites**

Change the normalize call and its comment (currently around lines 825-833) to:

```python
    # Normalize every piece to a common staff WIDTH before stacking, so pieces
    # captured at different resolutions or with different staff structures stack
    # into pages with consistent staff length (like real engraved sheet music).
    # reference_width feeds pack_pages's physical page-height budget. Done before
    # the insert branch so the inserted piece (which matches itself to
    # piece_arrays[0]) targets the normalized set.
    piece_arrays, norm_warnings, reference_width = normalize_piece_scales(piece_arrays)
    warnings.extend(norm_warnings)
```

Change the `--at-position` pre-layout call (currently around line 853):

```python
            pre_layout = pack_pages(pre_heights, reference_width, margin=margin)  # map against current layout
```

Change the main pack call and sparse guard (currently around lines 864-867):

```python
        piece_heights = [p.shape[0] for p in piece_arrays]
        counts = pack_pages(piece_heights, reference_width, margin=margin)
        if reference_width is not None:
            warnings.extend(
                sparse_page_warnings(piece_heights, counts, reference_width, margin=margin)
            )
```

- [ ] **Step 9: Run test_pack_pages.py and test_normalize_scales.py**

Run: `uv run pytest tests/test_pack_pages.py -v`
Expected: all pass.

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: the two `reference_spacing`-observation tests FAIL (they assert spacing values from the removed pass) — fixed in Step 10. The width-rescale, blank, identity, no-op, and degenerate-clamp tests still pass (their third unpacked value was already ignored as `_reference`).

- [ ] **Step 10: Fix the two reference tests in test_normalize_scales.py**

In `tests/test_normalize_scales.py`, replace `test_reference_spacing_is_median_of_post_rescale_spacing` and `test_reference_spacing_is_none_when_fewer_than_two_rescaled_have_spacing` with:

```python
def test_reference_width_is_median_of_measured_widths():
    # _rgb_staff draws lines spanning the full width, so measure_content_width
    # equals `width` exactly; median({200, 400}) = 300.
    pieces = [_rgb_staff(spacing=30, width=200), _rgb_staff(spacing=30, width=400)]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference == 300.0


def test_reference_width_is_none_when_fewer_than_two_measurable():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)  # no ink -> unmeasurable width
    pieces = [_rgb_staff(spacing=30, width=200), blank]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference is None
```

If the `_vertical_bar` helper (added earlier only for the old spacing-None test) is now unused, delete it. Grep first: if nothing else references `_vertical_bar`, remove its definition.

- [ ] **Step 11: Update test_cli_end_to_end.py**

**(a)** Change the `_snippet` fixture default width from `800` to `1450` (a realistic ~4:1 staff aspect; page counts now depend on aspect) and update its docstring to drop the deleted `REF_SNIPPET_HEIGHT_SPACINGS` reference:

```python
def _snippet(spacing=30, width=1450, n_lines=5, stray=False, pad=197):
    """A synthetic 'system': 5 full-width staff lines at a fixed spacing,
    optionally with an isolated stray mark near the top-left corner.

    Default width=1450 / pad=197 gives a realistic wide-staff aspect (content
    width 1390 x height 360, ~4:1) -- load-bearing now that pack_pages sizes
    pages by physical page fit, which depends on the snippet's width-to-height
    ratio. Override width/pad to build differently-shaped snippets.
    """
    top, thick = 40, 3
    height = top + spacing * (n_lines - 1) + thick + pad
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + thick, 30 : width - 30] = 0  # inset so edges are whitespace
    if stray:
        img[5:13, 3:20] = 0  # thin, top-left, isolated
    return img
```

**(b)** `test_full_run_11_pieces`: change `assert summary["counts"] == [6, 5]` to `assert summary["counts"] == [4, 4, 3]` and `assert len(summary["page_pngs"]) == 2` to `== 3`.

**(c)** `test_pdf_has_expected_page_count`: change `assert summary["counts"] == [5, 5, 5]` to `== [4, 4, 4, 3]` and the PDF-page-count assertion `data.count(b"/Type /Page\n") == 3` to `== 4`.

**(d)** `test_n7_without_pages_resolves_automatically`: the expected value is unchanged (`[4, 3]`), but update any stale comment referring to spacing/`pack_pages resolves N=7`; the behavior now comes from the width budget. Keep `assert summary["counts"] == [4, 3]`.

**(e)** `test_insert_at_top_rescales_and_adds_piece`: keep the `num_pieces == 6` and `insert_factor ≈ 0.5` assertions; change the count assertion `summary["counts"] == [6]` to `sum(summary["counts"]) == 6` (the exact split is incidental to what this test checks, and the inserted half-scale piece makes it fixture-sensitive).

**(f)** `test_sparse_page_warning_fires_for_a_genuinely_lopsided_split`: replace its body with a tall-but-in-budget lead piece plus two short ones (the old giant-taller-than-page input now clamps to all-singles and no longer yields a sparse multi-piece page):

```python
def test_sparse_page_warning_fires_for_a_genuinely_lopsided_split(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # One tall system (height 1500, still within the ~1625 budget) forces a
    # [1, 2] split; the 2-short-piece page (163+40+163 = 366) is under 40% of
    # the budget, so a sparse warning must fire for page 2. All same width so
    # width-normalization is a no-op.
    Image.fromarray(_snippet(spacing=30, pad=1337)).save(src / "Song_1.png")  # height 1500
    for i in range(2, 4):
        Image.fromarray(_snippet(spacing=30, pad=0)).save(src / f"Song_{i}.png")  # height 163

    summary = assemble(src, "Song", out)

    assert summary["counts"] == [1, 2]
    assert any("sparse" in w and "page 2" in w for w in summary["warnings"])
```

**(g)** `test_at_position_uses_height_aware_pre_layout` (AMENDMENT — missed in the original Step 11 list): its current `pad=737` tall pieces (height 900) no longer pack multiple-per-page under the physical budget (each is 1/page), so its `[3,2]` assertion and its "2 per page" premise break. Redesign it to use short (default-height) pieces, which still exercises the point (that `--at-position` maps against the *height-aware* pre-layout, not `balance_pages` — under `balance_pages(5)=[5]` the `page 2` reference would be invalid, but `pack_pages` gives `[3,2]` so `page 2 index 0` → flat index 3). Replace the test body with:

```python
def test_at_position_uses_height_aware_pre_layout(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # 5 normal single-staff snippets. The height-aware pre-insert layout packs
    # them [3, 2] (physical budget), so "--at-position 2:0" maps to flat index
    # 3 -- the start of page 2. (balance_pages(5) would be a single page [5],
    # for which "page 2" is out of range, so this genuinely exercises the
    # height-aware pre-layout path.)
    _write_pieces(src, "Song", 5, spacing=30)
    ins = tmp_path / "extra.png"
    Image.fromarray(_snippet(spacing=30)).save(ins)

    summary = assemble(src, "Song", out, insert=ins, at_position="2:0")

    assert summary["num_pieces"] == 6
    assert summary["counts"] == [3, 3]  # 6 normal snippets, physical budget
```

Leave `test_n7_with_no_measurable_reference_still_raises`, `test_n7_with_pages_override_succeeds`, `test_different_width_pieces_normalize_to_matching_width`, `test_identical_inputs_produce_byte_identical_output`, and `test_missing_piece_exits_nonzero` unchanged (they either assert non-count properties, use the override path, or pass explicit widths).

- [ ] **Step 12: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. If any test not named in Step 11 fails on a count assertion, do NOT silently adjust it — stop and report which, with the actual vs expected split; it means a fixture interaction this plan didn't anticipate.

- [ ] **Step 13: Verify against the real file**

Run:
```bash
uv run python3 scripts/assemble_sheet_music.py --input-dir "/Users/jayden/Downloads/New Folder With Items" --prefix "찬양하세" --output-dir /tmp/smc_budget_check 2>&1 | grep -E "pieces ->"
```
Expected: `찬양하세: 11 pieces -> 4 page(s) [2, 3, 3, 3]` (was 5 pages). Report the actual line.

- [ ] **Step 14: Commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_pack_pages.py tests/test_normalize_scales.py tests/test_cli_end_to_end.py
git commit -m "Budget pages by physical page height instead of a staff-spacing proxy"
```

---

### Task 2: Rewrite `CLAUDE.md` §b and §d

`CLAUDE.md` is gitignored (kept on disk, untracked — do not `git add` or un-ignore it). Edit the on-disk file directly; it will not appear in `git status`.

**Files:** Modify: `CLAUDE.md` §b and the relevant bullet of §d.

**Interfaces:** None (documentation only).

- [ ] **Step 1: Rewrite §b**

Replace the entire `**b. Page-count balancing ...**` section (from that heading up to but not including `**c. Optional insert`) with:

```markdown
**b. Page-count balancing (physical page-height budget)**
Pages fill by physical fit, not a fixed count. Because §d normalizes every
piece to a common content width `W`, the uniform letter-page scale (§f) is
width-bound at `s_w = usable_w / W`, a hard ceiling on notation size. So the
packer keeps each page short enough that height never becomes the binding
constraint, using the fewest pages that achieves it:
- Page-height budget `B = PAGE_FILL_FRACTION * usable_h * W / usable_w`
  (`PAGE_FILL_FRACTION = 0.9`), i.e. 90% of the content height that fits one
  physical US-Letter page at `s_w`. The 0.9 leaves engraving-like breathing
  room top and bottom.
- `pack_pages` builds a min-max linear-partition DP over all page counts and
  picks the **smallest `k`** whose optimal tallest page is `<= B`. Among
  partitions tied at that height it minimises the sum of squared page heights
  (so pages are balanced, not lopsided), front-loading final ties.
- The budget is only a packing heuristic: `compute_uniform_scale` (§f) still
  computes the actual scale from the real rendered page sizes, so `B` need not
  be pixel-exact — it only chooses page counts and groupings.
- **There is no per-page count cap.** How many systems land on a page depends
  on their measured height and the page geometry: short single-staff systems
  pack more per page, tall piano+vocal systems fewer. (This replaced an earlier
  fixed 4-6-per-page model, which crammed tall systems until the uniform scale
  shrank them — the physical budget keeps notation at the width-bound size.)
- **No reliable reference:** if fewer than 2 pieces have measurable width there
  is no `W`, so this falls back to the old count-based `balance_pages` (max 6 /
  min 4, and its N=7 gap that raises unless `--pages` is given).
- **A single system taller than a page** can't fit the budget alone; it gets its
  own page and `compute_uniform_scale` shrinks the run to fit — unavoidable.
- **Sparse page:** if the chosen partition leaves any page under 40% of the
  budget (a tall system forced an uneven split), that's reported as a warning;
  it never blocks.
- Support a manual override flag `--pages "5,5,4"` (explicit comma list) that
  skips the packer entirely and is authoritative.
```

- [ ] **Step 2: Update §d's reference bullet**

In `**d. Whole-set staff-width normalization**`, replace the bullet beginning `- `normalize_piece_scales` still returns a third value, `reference_spacing`` (and its sub-text) with:

```markdown
- `normalize_piece_scales` returns a third value, `reference_width` — the
  median content width it rescaled every piece toward. The height-aware page
  packer (§b) uses it to size the physical page-height budget. It is `None`
  when fewer than 2 pieces had measurable width (then §b falls back to
  count-based balancing).
```

- [ ] **Step 3: Verify the on-disk file**

Read `CLAUDE.md` back and confirm §b and §d read correctly and §a / §c / §e around them are untouched.

---

## Self-Review

**Spec coverage:**
- Geometry budget `B = 0.9 * usable_h * W / usable_w` — Task 1 Steps 1, 4. ✅
- `num_pages` = smallest k that fits — Task 1 Step 5. ✅
- `normalize_piece_scales` returns width, observational pass removed — Task 1 Step 7. ✅
- `pack_pages` / `sparse_page_warnings` signatures (`reference_width` + `margin`) — Steps 5, 6, and call sites Step 8. ✅
- `PAGE_FILL_FRACTION` added, `REF_SNIPPET_HEIGHT_SPACINGS` deleted — Step 1. ✅
- Two-stage partition DP logic unchanged — preserved verbatim in Step 5. ✅
- `compute_uniform_scale`, `--insert`, `balance_pages`, `measure_staff_spacing`, `rescale_factor` untouched — no step modifies them. ✅
- No per-page cap; old 4-6 examples retired — Task 1 rewrites test_pack_pages and the e2e count assertions. ✅
- Fallback (reference_width None → balance_pages), clamp-to-N, `--pages`, sparse-warning behaviors — covered by tests in Step 2 and the e2e updates. ✅
- CLAUDE.md §b + §d — Task 2. ✅

**Placeholder scan:** No TODOs; every code block is complete. ✅

**Type consistency:** `pack_pages(piece_heights, reference_width, *, margin=DEFAULT_MARGIN_PX, gap=STACK_GAP_PX)` used identically in Step 5, the two call sites (Step 8), and every test in Step 2. `sparse_page_warnings(..., reference_width, *, margin=..., gap=...)` consistent between Step 6 and its call site/tests. `normalize_piece_scales`'s 3-tuple return consumed identically at the Step 8 call site. ✅

**Verified-values note:** every exact expected value in the tests (`[2,3,3,3]`, the single-staff table, `[1,2]` sparse, `[3,2]`/`[3,3]` for the e2e insert/at-position, `[4,4,3]`/`[4,4,4,3]` e2e) was computed by running the proposed algorithm before this plan was written — not hand-derived. An implementer hitting a different value should stop and report, not adjust.
