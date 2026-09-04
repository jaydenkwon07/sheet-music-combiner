# Height-Based Page Packing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed 4–6-count page-balancing formula with a height-aware packer, so the number of snippets per page adapts to each snippet's measured pixel height instead of a flat count — tall voice+piano snippets land fewer per page, normal single-staff snippets keep today's splits.

**Architecture:** A new pure function `pack_pages(piece_heights, reference_spacing)` replaces `balance_pages(total)` as the no-override path in `assemble()`. It computes an absolute page-height budget `B` (a fixed multiple of the run's median staff spacing) and partitions the ordered piece list into the fewest contiguous, height-balanced groups that fit under it, via a linear-partition DP with a front-loaded tie-break. When no reliable spacing reference exists, it delegates unchanged to `balance_pages`. All logic lives in `scripts/assemble_sheet_music.py`; nothing outside it changes.

**Tech Stack:** Python 3.12, NumPy, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-height-based-page-packing-design.md`

## Global Constraints

- **Determinism holds.** Same inputs → same partition → same output bytes. The packer uses only measured pixel heights and integer/float arithmetic with a fixed tie-break rule — no randomness, no floating iteration order dependence.
- **The two parts stay separate.** All logic lives in `scripts/assemble_sheet_music.py`. `web/backend/assembler_bridge.py`, `web/`, and `.claude/agents/sheet-music-assembler.md` are not touched by this plan.
- **Backward compatible for the common case.** A set of uniform single-staff snippets must still produce today's splits: N=11→[6,5], N=15→[5,5,5], N=14→[5,5,4], and now N=7→[4,3] automatically (previously an error).
- **No LLM calls, no guessing.** Every function here is pure and deterministic.
- **Out of scope:** `stack_pieces`, `clean_stray_marks`, `crop_to_content`, `compute_uniform_scale`, `render_letter_page`, output naming, the PDF path. `--pages` override behaviour (`parse_pages_override`) is unchanged and stays authoritative.
- **`balance_pages`'s `PageBalanceError`/N=7 raise is retained** as the fallback for inputs with no measurable staff-spacing reference — it becomes dead code on the normal (measurable) path only.

---

### Task 1: Commit the already-built `normalize_piece_scales` work

This is a prerequisite the spec depends on (whole-set staff-spacing normalization, CLAUDE.md §d) — it's fully implemented and its 4 tests pass, but sits uncommitted in the working tree. Commit it as-is before making any further changes, so the packer work that depends on it starts from a clean base.

**Files:**
- Commit (no edits): `scripts/assemble_sheet_music.py` (existing uncommitted diff — `normalize_piece_scales`, `SCALE_NOOP_TOLERANCE`, its call site in `assemble()`)
- Commit (no edits): `tests/test_normalize_scales.py` (untracked, 4 tests)

**Interfaces:**
- Produces: `normalize_piece_scales(pieces: list[np.ndarray]) -> tuple[list[np.ndarray], list[str]]` — already implemented; Task 2 changes its signature.

- [ ] **Step 1: Confirm the existing work is green**

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: 4 passed (`test_mismatched_pieces_end_at_matching_spacing`, `test_unmeasurable_piece_is_left_alone_others_normalized`, `test_already_consistent_set_is_unchanged`, `test_fewer_than_two_measurable_returns_unchanged`).

- [ ] **Step 2: Stage and commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_normalize_scales.py
git commit -m "Normalize every piece to the run's median staff spacing before stacking"
```

- [ ] **Step 3: Verify clean**

Run: `git status`
Expected: `scripts/assemble_sheet_music.py` and `tests/test_normalize_scales.py` no longer listed as modified/untracked. (`.DS_Store` and `node_modules/` remain untracked — unrelated to this work, leave them.)

---

### Task 2: Expose the reference spacing from `normalize_piece_scales`

The packer needs the run's median staff spacing to build its height budget. Add it as a third return value.

**Files:**
- Modify: `scripts/assemble_sheet_music.py:229-271` (`normalize_piece_scales`), `:626` (its call site in `assemble()`)
- Test: `tests/test_normalize_scales.py`

**Interfaces:**
- Produces: `normalize_piece_scales(pieces) -> tuple[list[np.ndarray], list[str], float | None]` — third element is the median measured spacing, or `None` when fewer than 2 pieces were measurable.
- Consumes (Task 4): callers must now unpack 3 values and pass the spacing through to `pack_pages`.

- [ ] **Step 1: Update the existing tests to unpack 3 values, and add 2 new ones**

In `tests/test_normalize_scales.py`, change all 4 `out, warnings = normalize_piece_scales(pieces)` lines to `out, warnings, _reference = normalize_piece_scales(pieces)`, then append:

```python
def test_reference_spacing_is_the_median_of_measured():
    pieces = [_rgb_staff(spacing=30), _rgb_staff(spacing=60)]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference == 45.0


def test_reference_spacing_is_none_when_fewer_than_two_measurable():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)
    pieces = [_rgb_staff(spacing=30), blank]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: FAIL — the 4 updated tests fail with a tuple-unpacking error (function still returns 2 values); the 2 new tests fail the same way.

- [ ] **Step 3: Implement the third return value**

In `scripts/assemble_sheet_music.py`, change the signature and both `return` statements of `normalize_piece_scales` (lines 229-271):

```python
def normalize_piece_scales(
    pieces: list[np.ndarray],
) -> tuple[list[np.ndarray], list[str], float | None]:
    """Rescale every piece so its staff-line spacing matches a common reference
    (the MEDIAN measured spacing), so no snippet renders larger than the others
    just because it was exported at a different DPI.

    The median is used so one oddly-scaled outlier can't drag the reference. A
    piece whose spacing can't be measured (<2 staff lines -- e.g. a chord-only
    or lyric snippet) is left at native scale and reported. If fewer than 2
    pieces are measurable there is no reliable reference, so all are left as-is.

    Returns (possibly-rescaled pieces, human-readable warnings, reference
    spacing). ``reference_spacing`` is the median measured spacing --
    ``pack_pages`` uses it to build its height budget -- or None when fewer
    than 2 pieces were measurable (callers should fall back to count-based
    ``balance_pages``). Pieces already within SCALE_NOOP_TOLERANCE of the
    reference are returned unchanged (same object), so an already-consistent
    set is a true no-op.
    """
    spacings: list[float | None] = []
    warnings: list[str] = []
    for i, piece in enumerate(pieces, start=1):
        gray = piece[..., :3].mean(axis=2) if piece.ndim == 3 else piece.astype(np.float32)
        try:
            spacings.append(measure_staff_spacing(gray))
        except ValueError:
            spacings.append(None)
            warnings.append(f"piece {i}: staff spacing unmeasurable; left at native scale")

    measured = [s for s in spacings if s is not None]
    if len(measured) < 2:
        return pieces, warnings, None

    reference = float(np.median(measured))
    out: list[np.ndarray] = []
    for i, (piece, spacing) in enumerate(zip(pieces, spacings), start=1):
        if spacing is None:
            out.append(piece)
            continue
        factor = rescale_factor(reference, spacing)
        if abs(factor - 1.0) <= SCALE_NOOP_TOLERANCE:
            out.append(piece)
            continue
        out.append(resize_rgb(piece, factor))
        warnings.append(f"piece {i}: rescaled x{factor:.3f} to match staff spacing")
    return out, warnings, reference
```

- [ ] **Step 4: Update the call site in `assemble()`**

At line 626, change:

```python
    piece_arrays, norm_warnings = normalize_piece_scales(piece_arrays)
    warnings.extend(norm_warnings)
```

to:

```python
    piece_arrays, norm_warnings, reference_spacing = normalize_piece_scales(piece_arrays)
    warnings.extend(norm_warnings)
```

(`reference_spacing` is unused until Task 4 — that's expected, it's consumed there.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_normalize_scales.py tests/test_cli_end_to_end.py -v`
Expected: all pass (the full-suite `assemble()` tests must still pass since `assemble()`'s behavior is otherwise unchanged).

- [ ] **Step 6: Commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_normalize_scales.py
git commit -m "Expose the run's median staff spacing from normalize_piece_scales"
```

---

### Task 3: Build `pack_pages` and `sparse_page_warnings`

Pure, fully unit-tested height-aware packing logic. Not wired into `assemble()` yet (Task 4) — this task's deliverable is independently testable via direct calls.

**Files:**
- Modify: `scripts/assemble_sheet_music.py` — add `REF_SNIPPET_HEIGHT_SPACINGS` near line 22; add new functions after `flat_index_for_position` (currently ends at line 425, right before the `# Image I/O and rendering` comment block at line 428)
- Test: `tests/test_pack_pages.py` (new)

**Interfaces:**
- Consumes: `balance_pages(n: int) -> list[int]` (existing, unchanged), `STACK_GAP_PX`, `MAX_PER_PAGE` (existing constants), `_split_into_pages(items, counts)` (existing, at line 565).
- Produces:
  - `pack_pages(piece_heights: list[int], reference_spacing: float | None, *, gap: int = STACK_GAP_PX) -> list[int]` — per-page counts summing to `len(piece_heights)`.
  - `sparse_page_warnings(piece_heights: list[int], counts: list[int], reference_spacing: float, *, gap: int = STACK_GAP_PX) -> list[str]` — human-readable warnings for any page whose stacked height falls under 40% of the budget.
  - `REF_SNIPPET_HEIGHT_SPACINGS = 12.0` (module constant).

- [ ] **Step 1: Add the constant**

In `scripts/assemble_sheet_music.py`, after line 23 (`MIN_PER_PAGE = 4`), add:

```python
# A normal single-system snippet is ~this many staff-line spacings tall (4-gap
# staff + typical note/stem/ledger/lyric margin). Used to build an absolute,
# spacing-scaled page-height budget for pack_pages, instead of a fixed count.
REF_SNIPPET_HEIGHT_SPACINGS = 12.0
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_pack_pages.py`:

```python
"""Tests for the height-aware page packer (pack_pages / sparse_page_warnings).

Unlike balance_pages (a fixed count per page), pack_pages sizes pages by
measured pixel height, so unusually tall snippets (e.g. voice + piano) land
fewer per page. All heights below are synthetic ints standing in for
piece_arrays[i].shape[0] -- these tests never build real images.
"""

from assemble_sheet_music import (
    REF_SNIPPET_HEIGHT_SPACINGS,
    STACK_GAP_PX,
    balance_pages,
    pack_pages,
    sparse_page_warnings,
)


def test_n11_uniform_splits_6_5():
    assert pack_pages([120] * 11, reference_spacing=10.0) == [6, 5]


def test_n15_uniform_splits_5_5_5():
    assert pack_pages([120] * 15, reference_spacing=10.0) == [5, 5, 5]


def test_n14_uniform_splits_5_5_4():
    assert pack_pages([120] * 14, reference_spacing=10.0) == [5, 5, 4]


def test_n7_uniform_resolves_4_3_automatically():
    # The old count-based gap (balance_pages(7) raises) is gone on this path.
    assert pack_pages([120] * 7, reference_spacing=10.0) == [4, 3]


def test_tall_snippets_pack_fewer_per_page():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s  # 120.0
    heights = [round(2.5 * h_ref)] * 12  # 300 each, ~voice+piano height
    counts = pack_pages(heights, s)
    assert sum(counts) == 12
    # 12 uniform-300 items in 5 pages: some page must hold >= ceil(12/5) = 3
    # items, so the true minimum achievable max-group-height is 3*300+2*40 =
    # 980 -- pack_pages must actually reach that minimum (not just return
    # *a* valid partition). Multiple count-distributions tie at max=980
    # (e.g. [3,3,2,2,2] and [3,3,3,2,1]); this only checks the achieved
    # value, not which tied distribution was chosen.
    gap = 40
    group_heights, pos = [], 0
    for c in counts:
        group_heights.append(sum(heights[pos : pos + c]) + (c - 1) * gap)
        pos += c
    assert max(group_heights) == 980
    # Far fewer per page than the old fixed-count formula would give.
    assert max(counts) < max(balance_pages(12))


def test_mixed_tall_and_short_partitions_optimally_in_order():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s
    tall = round(2.5 * h_ref)  # 300
    short = round(h_ref)  # 120
    heights = [tall, tall, tall, short, short, short, short, short, short]
    counts = pack_pages(heights, s)
    assert counts == [2, 3, 4]
    # Order is never reordered -- reconstruct the groups and check the first
    # page is tall-only and the last page is short-only, matching input order.
    pos = 0
    groups = []
    for c in counts:
        groups.append(heights[pos : pos + c])
        pos += c
    assert groups[0] == [tall, tall]
    assert groups[-1] == [short, short, short, short]


def test_every_piece_taller_than_budget_gets_its_own_page():
    s = 10.0
    budget = 6 * REF_SNIPPET_HEIGHT_SPACINGS * s + 5 * STACK_GAP_PX  # 920.0
    huge = round(budget * 2)
    assert pack_pages([huge] * 5, s) == [1, 1, 1, 1, 1]


def test_no_reference_delegates_to_balance_pages():
    heights = [999] * 11  # heights irrelevant when reference_spacing is None
    assert pack_pages(heights, None) == balance_pages(11)


def test_pages_sum_to_total_for_arbitrary_heights():
    heights = [50, 400, 120, 900, 60, 60, 60, 200]
    assert sum(pack_pages(heights, 10.0)) == len(heights)


def test_sparse_warning_emitted_for_disproportionate_split():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s  # 120.0
    heights = [round(5 * h_ref)] + [round(0.5 * h_ref)] * 4  # 600, 60x4
    counts = [1, 4]  # a huge snippet alone; four modest ones on the other page
    warnings = sparse_page_warnings(heights, counts, s)
    assert any("sparse" in w and "page 2" in w for w in warnings)
    assert not any("page 1" in w for w in warnings)


def test_sparse_warning_not_emitted_for_single_page():
    assert sparse_page_warnings([1], [1], 10.0) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_pack_pages.py -v`
Expected: FAIL — `ImportError: cannot import name 'pack_pages'` (and `sparse_page_warnings`, `REF_SNIPPET_HEIGHT_SPACINGS` already added in Step 1 so that one import succeeds).

- [ ] **Step 4: Implement `pack_pages` and `sparse_page_warnings`**

In `scripts/assemble_sheet_music.py`, insert the following immediately after `flat_index_for_position` (which ends at line 425) and before the `# Image I/O and rendering` comment block (line 428):

```python
def _page_height_budget(reference_spacing: float, gap: int = STACK_GAP_PX) -> float:
    """Absolute page-height budget B: a fixed multiple of the reference staff
    spacing, not of this run's own average piece height -- so a run of
    unusually tall snippets produces MORE pages rather than being squeezed to
    fit the same page count as a run of normal ones. MAX_PER_PAGE (6) is kept
    as the anchor that reproduces balance_pages's splits for normal-height
    uniform inputs."""
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * reference_spacing
    return MAX_PER_PAGE * h_ref + (MAX_PER_PAGE - 1) * gap


def _group_height(heights: list[int], gap: int) -> float:
    """Stacked height of a contiguous run of pieces: their heights plus the
    gaps stack_pieces will insert between them."""
    if not heights:
        return 0.0
    return sum(heights) + (len(heights) - 1) * gap


def pack_pages(
    piece_heights: list[int],
    reference_spacing: float | None,
    *,
    gap: int = STACK_GAP_PX,
) -> list[int]:
    """Height-aware replacement for balance_pages: split the ordered piece
    list into contiguous per-page groups sized by measured pixel height
    rather than a fixed count, so unusually tall snippets (e.g. voice +
    piano) land fewer per page.

    ``reference_spacing`` is None when normalize_piece_scales found fewer
    than 2 measurable pieces (no reliable reference) -- in that case this
    delegates to the old count-based balance_pages unchanged (including its
    N=7 PageBalanceError).

    Otherwise: compute an absolute page-height budget B (a fixed multiple of
    the reference staff spacing), pick the smallest number of pages k whose
    budget can plausibly hold the total stacked height, then find the
    contiguous k-way partition minimising the tallest page (classic
    linear-partition DP), with ties broken toward fuller earlier pages
    (front-loaded, matching balance_pages's [6,5]-not-[5,6] convention).
    """
    n = len(piece_heights)
    if reference_spacing is None:
        return balance_pages(n)

    budget = _page_height_budget(reference_spacing, gap)
    total = _group_height(piece_heights, gap)
    num_pages = max(1, min(n, math.ceil(total / budget)))

    if num_pages == 1:
        return [n]

    # prefix_dp[i][j]: min possible "tallest group" height when partitioning
    # the FIRST i pieces into j contiguous groups. suffix_dp is the same
    # recurrence run on the reversed list, so suffix_dp[i][j] is the min
    # possible tallest-group height for the LAST i pieces in j groups --
    # group height only depends on a run's sum and count, not its order, so
    # reversing is a valid way to get suffix feasibility from the same DP.
    def build_dp(heights: list[int]) -> list[list[float]]:
        m = len(heights)
        dp = [[math.inf] * (num_pages + 1) for _ in range(m + 1)]
        dp[0][0] = 0.0
        for j in range(1, num_pages + 1):
            for i in range(j, m + 1):
                best = math.inf
                for split in range(j - 1, i):
                    candidate = max(dp[split][j - 1], _group_height(heights[split:i], gap))
                    if candidate < best:
                        best = candidate
                dp[i][j] = best
        return dp

    prefix_dp = build_dp(piece_heights)
    suffix_dp = build_dp(list(reversed(piece_heights)))
    target = prefix_dp[n][num_pages]

    counts: list[int] = []
    pos = 0
    remaining_groups = num_pages
    while remaining_groups > 0:
        if remaining_groups == 1:
            counts.append(n - pos)
            break
        for end in range(n - 1, pos - 1, -1):  # end is inclusive, 0-indexed
            height = _group_height(piece_heights[pos : end + 1], gap)
            if height > target:
                continue
            suffix_len = n - (end + 1)
            if suffix_dp[suffix_len][remaining_groups - 1] <= target:
                counts.append(end - pos + 1)
                pos = end + 1
                remaining_groups -= 1
                break
    return counts


def sparse_page_warnings(
    piece_heights: list[int],
    counts: list[int],
    reference_spacing: float,
    *,
    gap: int = STACK_GAP_PX,
) -> list[str]:
    """Warn (never block) when the height-aware packer left a page markedly
    emptier than the others -- a very tall snippet elsewhere forced the
    partition uneven. Same channel as stray-mark warnings; never fires for a
    single-page result."""
    if len(counts) <= 1:
        return []
    budget = _page_height_budget(reference_spacing, gap)
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pack_pages.py -v`
Expected: 11 passed.

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest -q`
Expected: all pass (this task doesn't touch `assemble()`'s call sites yet, so nothing else should move).

- [ ] **Step 7: Commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_pack_pages.py
git commit -m "Add pack_pages: a height-aware replacement for count-based page balancing"
```

---

### Task 4: Wire `pack_pages` into `assemble()`

Replace the two call sites that currently use `balance_pages` for the automatic (no `--pages` override) path: the main page-count computation, and the `--at-position` pre-insert layout mapping. Update the existing end-to-end fixture so it models a realistic single-staff snippet height (previously it only needed realistic *spacing*, not overall height, so it's much shorter than `REF_SNIPPET_HEIGHT_SPACINGS` assumes — this must change for the "backward compatible for the common case" constraint to actually hold against real pipeline runs, not just the isolated `pack_pages` unit tests from Task 3).

**Files:**
- Modify: `scripts/assemble_sheet_music.py:653-657` (main page-count computation), `:642-649` (`--at-position` pre-insert mapping)
- Modify: `tests/test_cli_end_to_end.py` — recalibrate the `_snippet()` fixture's default height, add a `pad` parameter, replace the N=7-raises test, add 2 new integration tests

**Interfaces:**
- Consumes: `pack_pages(piece_heights, reference_spacing, *, gap=STACK_GAP_PX) -> list[int]`, `sparse_page_warnings(piece_heights, counts, reference_spacing, *, gap=STACK_GAP_PX) -> list[str]` (Task 3), `reference_spacing` (local variable in `assemble()`, from Task 2).

- [ ] **Step 1: Recalibrate the end-to-end fixture's height**

In `tests/test_cli_end_to_end.py`, the current `_snippet()` gives a snippet total height of ~203px at spacing=30 (ratio ~6.8x spacing) — much shorter than a realistic single-staff snippet's ~12x-spacing height that `REF_SNIPPET_HEIGHT_SPACINGS` is calibrated against. Change:

```python
def _snippet(spacing=30, width=800, n_lines=5, stray=False):
    """A synthetic 'system': 5 full-width staff lines at a fixed spacing,
    optionally with an isolated stray mark near the top-left corner."""
    top, thick, pad = 40, 3, 40
    height = top + spacing * (n_lines - 1) + thick + pad
```

to:

```python
def _snippet(spacing=30, width=800, n_lines=5, stray=False, pad=200):
    """A synthetic 'system': 5 full-width staff lines at a fixed spacing,
    optionally with an isolated stray mark near the top-left corner.

    Default pad=200 gives a total height ~12x the staff spacing (40 + 30*4 +
    3 + 200 = 363 at spacing=30), matching REF_SNIPPET_HEIGHT_SPACINGS, so
    this fixture models a realistic single-staff snippet's height, not just
    its staff-line spacing -- load-bearing now that pack_pages sizes pages by
    height. Override pad to build an unusually tall ("voice + piano") snippet.
    """
    top, thick = 40, 3
    height = top + spacing * (n_lines - 1) + thick + pad
```

- [ ] **Step 2: Replace the N=7-raises test with the new automatic-resolution behavior**

Delete `test_n7_without_pages_exits_nonzero` and replace it with:

```python
def test_n7_without_pages_resolves_automatically(tmp_path):
    # The height-aware packer removes the old N=7 gap: with a measurable
    # reference spacing, pack_pages resolves N=7 to [4, 3] without an error.
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 7)
    summary = assemble(src, "Song", out)
    assert summary["counts"] == [4, 3]
    assert summary["pdf"].exists()


def test_n7_with_no_measurable_reference_still_raises(tmp_path):
    # No staff lines anywhere -> normalize_piece_scales returns reference
    # spacing None -> pack_pages delegates to balance_pages(7), which still
    # raises for the N=7 gap. The fallback path keeps today's behaviour;
    # only the height-aware (measurable-reference) path resolves N=7
    # automatically.
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    for i in range(1, 8):
        Image.fromarray(np.full((120, 200, 3), 255, dtype=np.uint8)).save(
            src / f"Song_{i}.png"
        )
    code = main([
        "--input-dir", str(src),
        "--prefix", "Song",
        "--output-dir", str(out),
    ])
    assert code == 2
```

- [ ] **Step 3: Add an `--at-position` integration test exercising the height-aware pre-layout**

Append to `tests/test_cli_end_to_end.py`:

```python
def test_at_position_uses_height_aware_pre_layout(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # 4 "tall" snippets (~2.5x a normal single-staff height) -- height-aware
    # packing splits these into 2 pages of 2 each, unlike balance_pages(4),
    # which would give a single page of 4 (no measurable-height awareness).
    _write_pieces(src, "Song", 4, spacing=30, pad=737)
    ins = tmp_path / "extra.png"
    Image.fromarray(_snippet(spacing=30, pad=737)).save(ins)

    summary = assemble(src, "Song", out, insert=ins, at_position="2:0")

    assert summary["num_pieces"] == 5
    # flat_index_for_position([2, 2], page=2, index=0) == 2: the pre-insert
    # layout put 2 tall snippets per page, so "page 2 index 0" lands right
    # after the first 2 originals. Post-insert, 5 uniform tall pieces pack
    # to [3, 2] (front-loaded, same base/remainder rule as the count-based
    # formula).
    assert summary["counts"] == [3, 2]
```

- [ ] **Step 4: Run the full end-to-end file to verify these fail correctly**

Run: `uv run pytest tests/test_cli_end_to_end.py -v`
Expected: FAIL — `test_n7_without_pages_resolves_automatically` gets a `PageBalanceError`-driven nonzero-equivalent instead of `[4, 3]` (since `assemble()` still calls `balance_pages`); `test_at_position_uses_height_aware_pre_layout` gets `[4]` or similar instead of `[3, 2]`. The other existing tests (`test_full_run_11_pieces`, `test_pdf_has_expected_page_count`, `test_insert_at_top_rescales_and_adds_piece`, `test_identical_inputs_produce_byte_identical_output`, `test_missing_piece_exits_nonzero`, `test_n7_with_pages_override_succeeds`) should still pass — the recalibrated `pad` was chosen so they keep producing the same counts once `pack_pages` is wired in.

- [ ] **Step 5: Wire `pack_pages` into the automatic page-count computation**

In `scripts/assemble_sheet_music.py`, change lines 653-657 from:

```python
    total = len(piece_arrays)
    if pages_spec is not None:
        counts = parse_pages_override(pages_spec, total)
    else:
        counts = balance_pages(total)  # may raise PageBalanceError (N=7 gap)
```

to:

```python
    total = len(piece_arrays)
    if pages_spec is not None:
        counts = parse_pages_override(pages_spec, total)
    else:
        piece_heights = [p.shape[0] for p in piece_arrays]
        counts = pack_pages(piece_heights, reference_spacing)
        if reference_spacing is not None:
            warnings.extend(sparse_page_warnings(piece_heights, counts, reference_spacing))
```

- [ ] **Step 6: Wire `pack_pages` into the `--at-position` pre-insert mapping**

Change lines 642-649 from:

```python
        if at_top:
            flat_index = 0
        else:
            page_str, _, idx_str = at_position.partition(":")
            pre_layout = balance_pages(len(paths))  # map against current layout
            flat_index = flat_index_for_position(
                pre_layout, int(page_str), int(idx_str)
            )
```

to:

```python
        if at_top:
            flat_index = 0
        else:
            page_str, _, idx_str = at_position.partition(":")
            pre_heights = [p.shape[0] for p in piece_arrays]
            pre_layout = pack_pages(pre_heights, reference_spacing)  # map against current layout
            flat_index = flat_index_for_position(
                pre_layout, int(page_str), int(idx_str)
            )
```

- [ ] **Step 7: Run the full suite to verify everything passes**

Run: `uv run pytest -q`
Expected: all pass, including every test in `tests/test_cli_end_to_end.py`, `tests/test_pack_pages.py`, `tests/test_normalize_scales.py`, `tests/test_balance_pages.py` (unchanged — `balance_pages` itself is untouched), `tests/test_insert_position.py` (unchanged — `flat_index_for_position`'s signature is untouched), `tests/test_memory_guard.py` (unaffected — its fixture's N=5 never exercises the multi-page path).

- [ ] **Step 8: Commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_cli_end_to_end.py
git commit -m "Wire the height-aware packer into assemble(), replacing balance_pages on the normal path"
```

---

### Task 5: Rewrite `CLAUDE.md` §b

Bring the contract file's description of page-count balancing in line with the new behavior. This is the spec's own final sequencing step.

**Files:**
- Modify: `CLAUDE.md:35-54`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Replace §b**

In `CLAUDE.md`, replace lines 35-54 (the entire `**b. Page-count balancing**` section, from that heading up to but not including the `**c. Optional insert` heading) with:

```markdown
**b. Page-count balancing (height-aware)**
Given the ordered list of pieces (after whole-set staff-spacing
normalization, §d), pack them onto pages by measured pixel **height**, not a
fixed count:
- `s` = the run's median staff-line spacing (from `normalize_piece_scales`).
  `h_ref = 12.0 * s` (`REF_SNIPPET_HEIGHT_SPACINGS`) is the reference height
  of one normal single-system snippet — a 4-gap staff plus typical
  note/stem/ledger/lyric margin.
- Page height budget `B = 6 * h_ref + 5 * STACK_GAP_PX` — an **absolute**
  budget (a fixed multiple of `s`), not a multiple of this run's own average
  piece height. That's what lets unusually tall snippets (e.g. voice +
  piano, ~2.5x a normal staff) produce MORE pages rather than being
  squeezed to fit the same page count as a run of normal ones.
- `num_pages = clamp(ceil(total_height / B), 1, N)`, where `total_height` is
  the sum of every piece's measured height plus `(N-1)` stacking gaps.
- Split the ordered pieces into exactly `num_pages` **contiguous** groups
  (order is fixed — snippets are a sequence, never reordered) minimizing
  the tallest group's height: a linear-partition DP. Ties are broken toward
  fuller earlier pages (front-loaded), the same `[6,5]`-not-`[5,6]`
  convention as before.
- Verified examples for uniform single-staff inputs (must match exactly):
  - N=11 → 2 pages: [6, 5]
  - N=15 → 3 pages: [5, 5, 5]
  - N=14 → 3 pages: [5, 5, 4]
  - N=7 → 2 pages: [4, 3] — resolved automatically now, no prompt.
- **No reliable reference:** if fewer than 2 pieces have measurable staff
  spacing, there's no `s` to build a budget from, so this falls back to the
  old count-based formula unchanged (max 6/min 4 per page). In that
  fallback only, N=7 still has no valid split (1 page of 7 is over max; 2
  pages of 4+3 breaks the min on one page) and raises rather than
  guessing — pass an explicit `--pages` override to resolve it.
- **Sparse page:** if the chosen partition leaves any page under 40% of the
  height budget (a very tall snippet elsewhere forced an uneven split),
  that's reported as a warning in the run summary — it never blocks.
- Support a manual override flag `--pages "5,5,4"` (explicit comma list)
  for cases the caller wants to control directly. It skips the packer
  entirely and is authoritative even outside the normal band.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Rewrite CLAUDE.md page-count balancing section for the height-aware packer"
```

---

## Self-Review

**Spec coverage:**
- Dependency ordering (commit WIP → add reference_spacing → build packer → wire in → rewrite CLAUDE.md) — Tasks 1-5, matches the spec's own 5-step sequence exactly. ✅
- `normalize_piece_scales` third return value — Task 2. ✅
- `pack_pages` signature, budget formula, DP partition, front-loaded tie-break — Task 3. ✅
- `balance_pages` retained unchanged as fallback, its N=7 raise preserved for that fallback only — Task 3 (unchanged) + Task 4 Step 2 test (`test_n7_with_no_measurable_reference_still_raises`) locks this in. ✅
- `REF_SNIPPET_HEIGHT_SPACINGS = 12.0` constant — Task 3 Step 1. ✅
- All 4 verified examples (N=11, 15, 14, 7) — Task 3 unit tests + Task 4 end-to-end tests (via the recalibrated fixture). ✅
- Sparse-page warning, 40% threshold, message format — Task 3 (`sparse_page_warnings`) + wired into `assemble()` in Task 4 Step 5. ✅
- Clamp to N when every piece exceeds the budget — Task 3 test. ✅
- `--pages` override unaffected — untouched code path; `parse_pages_override` not modified by any task. ✅
- `--at-position` pre-insert mapping uses the new packer — Task 4 Step 6, tested in Step 3. ✅
- Determinism — `pack_pages` is pure (no randomness); existing `test_identical_inputs_produce_byte_identical_output` continues to cover this without modification. ✅
- CLAUDE.md §b rewrite, dropping the "print two options" framing as primary behavior — Task 5. ✅
- Out-of-scope items (`stack_pieces`, `clean_stray_marks`, `crop_to_content`, `compute_uniform_scale`, `render_letter_page`, output naming, PDF path, web/subagent layers) — none touched by any task. ✅

**Placeholder scan:** No TODOs, no "add appropriate handling," no unfilled test bodies — every step has complete code. ✅

**Type consistency:** `pack_pages(piece_heights: list[int], reference_spacing: float | None, *, gap: int = STACK_GAP_PX) -> list[int]` used identically in Task 3's implementation and Task 4's two call sites. `sparse_page_warnings(piece_heights, counts, reference_spacing, *, gap=STACK_GAP_PX) -> list[str]` likewise. `normalize_piece_scales`'s new 3-tuple return is consumed identically in Task 2's call-site edit and referenced by name (`reference_spacing`) in Task 4. ✅

**Amendment (found during Task 3 execution):** `test_tall_snippets_pack_fewer_per_page`'s originally-specified exact expected value `[3, 3, 2, 2, 2]` was wrong — hand-tracing the DP+greedy-reconstruction algorithm exactly as specified (front-loaded: always take the largest height-feasible, suffix-feasible group at each step) actually yields `[3, 3, 3, 2, 1]` for this input. Both partitions tie at the true optimal max-group-height (980), so the algorithm is correct; only the plan's hand-derived exact-value assertion was in error. The test above now checks the properties the algorithm actually guarantees (sum, achieved max-height equals the proven optimum, fewer-per-page than `balance_pages`) instead of asserting one specific tied distribution. The algorithm itself was not changed.

**One design decision made explicit here (not fully pinned down in the spec's "Open questions"):** the spec's "Interface changes" section (item 3) states definitively that `balance_pages`'s N=7 raise "stays for the fallback," while a separate "Open questions" note says the author is "leaning yes" toward dropping it there too. This plan follows the definitive Interface-changes text (keep the fallback's raise) since it's the more authoritative statement, and locks the decision in with `test_n7_with_no_measurable_reference_still_raises` (Task 4). If Jayden wants the fallback's N=7 raise dropped too, that's a small follow-up: remove the `MIN_PER_PAGE` check from `balance_pages`, delete that regression test, and update CLAUDE.md's "No reliable reference" bullet.
