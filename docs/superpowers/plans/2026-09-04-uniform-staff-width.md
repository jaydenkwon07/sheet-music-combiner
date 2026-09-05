# Uniform Staff Width Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `normalize_piece_scales`'s scaling basis from staff-line spacing (note size) to content width (staff length), so snippets captured at different resolutions or with different staff structures stack into pages with visually consistent staff length — matching how real engraved sheet music looks.

**Architecture:** A new `measure_content_width` function (parallel to the existing `measure_staff_spacing`, reusing the same ink-mask technique `crop_to_content` already uses for its vertical extent) becomes the measurement `normalize_piece_scales` rescales by. `normalize_piece_scales`'s external interface — signature, call site, position in the pipeline — is completely unchanged; only its internal measurement changes, plus a new second pass that *observes* (does not control) post-rescale staff spacing to keep feeding `pack_pages`'s height budget unchanged.

**Tech Stack:** Python 3.12, NumPy, Pillow, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-uniform-staff-width-design.md`

**Amendment (final whole-branch review, opus, run against the real 11-piece file):**
The review confirmed the feature works (all 11 systems now share one staff width; the
two systems the old code destroyed at 0.32x render correctly) but found one Critical
robustness regression and several doc/scope items. A single fix wave addressed:

1. **Critical — unbounded rescale factor → potential OOM.** `measure_content_width`
   returns a valid but meaningless small value (e.g. `1`) for a near-blank piece (one
   dark pixel, a dust speck), so `rescale_factor(reference, 1)` yielded the full
   reference width as the factor — a ~1455x upscale on the real file's reference,
   ballooning a piece to a multi-hundred-MP array. The web memory guard estimates size
   from file headers *before* load, so it cannot see a post-load rescale — a near-blank
   upload could OOM the 512 MB Render tier. This is a NEW failure mode (the old
   spacing-based code raised ValueError on <2 staff lines and left such a piece alone).
   **Fix (controller-verified before dispatch by running the actual code):** clamp the
   applied factor to a `[WIDTH_RESCALE_MIN_FACTOR=0.2, WIDTH_RESCALE_MAX_FACTOR=5.0]`
   band; a factor outside it means an implausible width, so the piece is left at native
   scale with a warning — reusing the existing "unmeasurable → native scale" behavior.
   Verified: the real file's largest legitimate factor is 1.94 (well inside the band, so
   real output is unchanged), the one-pixel degenerate case is left at 60x40 not
   12000x8000, and all pre-existing tests still pass. A new test locks in the degenerate
   case.
2. **Two stale code comments** (the call-site comment and the `SCALE_NOOP_TOLERANCE`
   comment) still described spacing-based normalization — updated to width.
3. **CLAUDE.md §d caveats:** the background-wash limitation (`measure_content_width`
   measures the video-frame width, not the staff, on the majority of pieces in this file
   because the light-gray piano-key watermark registers as ink under `DARK_CUTOFF` — it
   happened to give the right answer only because the staff nearly fills the frame there)
   and a strengthened `--insert` warning (an inserted piece can be scaled *several times*
   wrong, not just "may not be consistent," because its reference is piece 0's possibly-
   mismeasured spacing).
4. **Dead imports** in the rewritten `tests/test_normalize_scales.py` removed.

**Parked as follow-ups (NOT in this branch — flagged to Jayden):**
- **Page-height budget is wasteful on real multi-staff input** (the review's Issue 5):
  the real file packs to 5 pages ~half-empty where 3 would fit at the same scale, because
  `REF_SNIPPET_HEIGHT_SPACINGS = 12` models a single-staff snippet but these are
  ~28-30-spacing piano+vocal systems. This is *mostly pre-existing* (the base also
  mis-packs, at 4 pages) and partly sharpened here (`reference_spacing` is now a sample
  from a heterogeneous population, not a shared value). It needs its own design decision
  (budget from usable letter-page height vs. a spacing proxy) — separate spec, not a
  fix-wave item.
- **The `measure_content_width` background-wash fragility** deeper fix (measure the
  staff-line span rather than the raw ink bbox) — documented as a §d caveat now; a real
  future improvement, out of scope here.
- **The `--insert` spacing-based rescale** remains the last consumer depending on
  `measure_staff_spacing` being right; revisit when the separate between-staff
  spacing-measurement bug is fixed.
- Review Minors 7 (double float pass), 8 (e2e test is partly unit-shaped), 9 (extract
  `observe_reference_spacing`) — polish, deferred.

## Global Constraints

- **Isotropic rescale only.** Every rescale in this codebase scales both axes by the same factor — this plan does not introduce horizontal-only stretching.
- **Determinism holds.** Same inputs → same rescale factors → same output bytes. `SCALE_NOOP_TOLERANCE`'s identity-preservation (a piece already within tolerance returns the *same object*, unresampled) carries over unchanged.
- **`normalize_piece_scales`'s external interface is unchanged:** `(pieces: list[np.ndarray]) -> (list[np.ndarray], list[str], float | None)`, same call site in `assemble()`, same pipeline position (before the `--insert` splice, before `pack_pages`). `pack_pages`, `sparse_page_warnings`, and everything else downstream needs zero code changes.
- **Out of scope:** `--insert`'s own rescale (still staff-spacing-based, unchanged — a known follow-up, not solved here); `stack_pieces`, `clean_stray_marks`, `crop_to_content`, `compute_uniform_scale`, `pack_pages`, `sparse_page_warnings`, `balance_pages`, `measure_staff_spacing`, `rescale_factor`, `resize_rgb` — none of these are modified.

---

### Task 1: `measure_content_width` and the rewritten `normalize_piece_scales`

**Files:**
- Modify: `scripts/assemble_sheet_music.py` — add `measure_content_width` after `measure_staff_spacing` (currently ends at line 223, before `rescale_factor` at line 226); rewrite `normalize_piece_scales` (currently lines 234-280)
- Replace entirely: `tests/test_normalize_scales.py` (today's 6 tests are all about spacing being the controlled variable — this task replaces the whole file, not adds to it)

**Interfaces:**
- Produces: `measure_content_width(rgb: np.ndarray) -> int` — pixel width of the tightest ink bounding box; raises `ValueError` on a blank (no-ink) input.
- Produces: `normalize_piece_scales(pieces) -> (list[np.ndarray], list[str], float | None)` — same signature as today. Third element (`reference_spacing`) is now a post-rescale *observation*, not a controlled quantity — see Task 1 Step 5.
- Consumes: `_to_ink_mask` (existing, unchanged), `measure_staff_spacing` (existing, unchanged — now used only for the observational second pass), `rescale_factor`, `resize_rgb`, `SCALE_NOOP_TOLERANCE` (all existing, unchanged).

- [ ] **Step 1: Write the new test file (failing)**

Replace the entire contents of `tests/test_normalize_scales.py` with:

```python
"""Tests for whole-set staff-width normalization (normalize_piece_scales).

A snippet captured at a different resolution, or with a different staff
structure (e.g. a piano-only excerpt vs. a piano+vocal system), has a
different pixel content width and would otherwise stack into a page with a
visibly different staff length than the rest -- unlike real engraved sheet
music, where every system on a page is the same width. normalize_piece_scales
rescales every piece to a common (median) content width so the assembled page
looks uniform.
"""

import numpy as np

from assemble_sheet_music import (
    measure_content_width,
    measure_staff_spacing,
    normalize_piece_scales,
)


def _rgb_staff(spacing, n_lines=5, width=200, line_thickness=2, top=20):
    """White RGB image with `n_lines` full-width black rows at `spacing`.

    Lines span every column (0..width-1), so this fixture's measured content
    width is exactly `width` -- convenient for testing width-based behavior
    directly, the same way it was already used for spacing-based behavior.
    """
    height = top + spacing * (n_lines - 1) + line_thickness + 20
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + line_thickness, :, :] = 0
    return img


def _vertical_bar(width=200, height=120, bar_width=4):
    """White RGB image with a single thin vertical bar: has measurable
    content width, but no full-width horizontal row, so
    measure_staff_spacing can never find a staff line on it."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    x0 = width // 2
    img[:, x0 : x0 + bar_width, :] = 0
    return img


def _gray(rgb):
    return rgb[..., :3].mean(axis=2)


def test_mismatched_widths_end_at_matching_width():
    pieces = [_rgb_staff(spacing=30, width=200), _rgb_staff(spacing=30, width=400)]
    out, warnings, _reference = normalize_piece_scales(pieces)
    # Median of {200, 400} is 300, so both are rescaled toward it.
    w0 = measure_content_width(out[0])
    w1 = measure_content_width(out[1])
    assert abs(w0 - w1) <= 2
    # The 400-wide piece was shrunk; the 200-wide piece was grown.
    assert out[1].shape[1] < pieces[1].shape[1]
    assert out[0].shape[1] > pieces[0].shape[1]
    assert len(warnings) == 2


def test_blank_piece_left_alone_others_normalized_by_width():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)  # no ink at all
    pieces = [
        _rgb_staff(spacing=30, width=200),
        blank,
        _rgb_staff(spacing=30, width=400),
    ]
    out, warnings, _reference = normalize_piece_scales(pieces)
    assert out[1] is pieces[1]  # blank untouched (identity)
    assert any("unmeasurable" in w for w in warnings)
    # The two measurable pieces still got normalized to each other.
    w0 = measure_content_width(out[0])
    w2 = measure_content_width(out[2])
    assert abs(w0 - w2) <= 2


def test_already_consistent_width_set_is_unchanged():
    pieces = [_rgb_staff(spacing=30, width=200) for _ in range(3)]
    out, warnings, _reference = normalize_piece_scales(pieces)
    assert warnings == []
    for original, result in zip(pieces, out):
        assert result is original  # within tolerance -> no resample


def test_fewer_than_two_measurable_widths_returns_unchanged():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)
    pieces = [_rgb_staff(spacing=30, width=200), blank]
    out, _warnings, _reference = normalize_piece_scales(pieces)
    for original, result in zip(pieces, out):
        assert result is original


def test_reference_spacing_is_median_of_post_rescale_spacing():
    # Different widths (so a real width-rescale happens) AND different
    # original spacings, so this distinguishes "median of RESCALED spacing"
    # from "median of raw spacing" ({20, 30} -> 25, the wrong answer -- that
    # would mean reference_spacing was measured before rescaling, not after).
    pieces = [_rgb_staff(spacing=20, width=200), _rgb_staff(spacing=30, width=400)]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    # Median width 300 -> factors 1.5 and 0.75 -> post-rescale spacings
    # ~30 and ~22.5 -> median ~26.25.
    assert abs(reference - 26.25) <= 2


def test_reference_spacing_is_none_when_fewer_than_two_rescaled_have_spacing():
    # Both pieces are width-measurable (so the rescale runs), but the second
    # has no staff lines at all -- only one of the two rescaled outputs can
    # ever yield a measurable spacing.
    pieces = [_rgb_staff(spacing=30, width=200), _vertical_bar(width=400)]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: FAIL — `ImportError: cannot import name 'measure_content_width'`.

- [ ] **Step 3: Add `measure_content_width`**

In `scripts/assemble_sheet_music.py`, insert immediately after `measure_staff_spacing` (which currently ends at line 223) and before `rescale_factor` (line 226):

```python
def measure_content_width(rgb: np.ndarray) -> int:
    """Pixel width of the tightest bounding box around non-background ink --
    the same ink-mask technique crop_to_content uses for its vertical extent,
    here measuring the horizontal extent instead.

    Raises ValueError if the piece has no detectable ink (blank).
    """
    mask = _to_ink_mask(np.asarray(rgb))
    cols = np.flatnonzero(mask.any(axis=0))
    if len(cols) == 0:
        raise ValueError("No ink detected; cannot measure content width")
    return int(cols[-1] - cols[0] + 1)
```

(`_to_ink_mask` is defined later in the file, at what is currently line 283 — this is fine; Python resolves module-level names at call time, and the same forward-reference pattern already exists elsewhere in this file.)

- [ ] **Step 4: Run tests to verify the import succeeds and confirm the remaining failures**

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: `measure_content_width` import succeeds; tests that call `normalize_piece_scales` still fail or produce wrong results, since its internals haven't changed yet (e.g. `test_mismatched_widths_end_at_matching_width` will fail because `normalize_piece_scales` is still rescaling by spacing, not width — spacing is identical (30) for both pieces in that test, so no rescale happens at all and the widths stay at their original 200/400).

- [ ] **Step 5: Rewrite `normalize_piece_scales`**

Replace the entire function body (currently lines 234-280) with:

```python
def normalize_piece_scales(
    pieces: list[np.ndarray],
) -> tuple[list[np.ndarray], list[str], float | None]:
    """Rescale every piece so its content WIDTH matches a common reference
    (the MEDIAN measured width), so pieces captured at different resolutions
    or with different staff structures (e.g. a piano-only excerpt vs. a
    piano+vocal system) still stack into pages with visually consistent
    staff length -- matching how printed/engraved scores keep every system
    on a page the same width, letting note spacing absorb the difference.

    The median is used so one oddly-scaled outlier (e.g. a title-block page)
    can't drag the reference. A piece whose width can't be measured (blank --
    no detectable ink) is left at native scale and reported. If fewer than 2
    pieces are measurable there is no reliable reference, so all are left
    as-is.

    Returns (possibly-rescaled pieces, human-readable warnings, reference
    spacing). ``reference_spacing`` is no longer the controlled quantity
    (width is) -- it's the median staff-line spacing OBSERVED on the
    width-rescaled results, a representative sample of this run's note size
    that ``pack_pages`` uses to build its height budget. It's None when
    fewer than 2 of the (width-rescaled) pieces have measurable spacing.
    Pieces already within SCALE_NOOP_TOLERANCE of the width reference are
    returned unchanged (same object), so an already-consistent set is a true
    no-op.
    """
    widths: list[int | None] = []
    warnings: list[str] = []
    for i, piece in enumerate(pieces, start=1):
        try:
            widths.append(measure_content_width(piece))
        except ValueError:
            widths.append(None)
            warnings.append(f"piece {i}: content width unmeasurable; left at native scale")

    measured_widths = [w for w in widths if w is not None]
    if len(measured_widths) < 2:
        return pieces, warnings, None

    reference_width = float(np.median(measured_widths))
    out: list[np.ndarray] = []
    for i, (piece, width) in enumerate(zip(pieces, widths), start=1):
        if width is None:
            out.append(piece)
            continue
        factor = rescale_factor(reference_width, width)
        if abs(factor - 1.0) <= SCALE_NOOP_TOLERANCE:
            out.append(piece)
            continue
        out.append(resize_rgb(piece, factor))
        warnings.append(f"piece {i}: rescaled x{factor:.3f} to match staff width")

    spacings: list[float] = []
    for piece in out:
        gray = piece[..., :3].mean(axis=2) if piece.ndim == 3 else piece.astype(np.float32)
        try:
            spacings.append(measure_staff_spacing(gray))
        except ValueError:
            continue
    reference_spacing = float(np.median(spacings)) if len(spacings) >= 2 else None

    return out, warnings, reference_spacing
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_normalize_scales.py -v`
Expected: 6 passed.

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `uv run pytest -q`
Expected: all pass. In particular, `tests/test_cli_end_to_end.py` and `tests/test_pack_pages.py` must be unaffected — `normalize_piece_scales`'s external contract (signature, warnings-as-list-of-strings shape, `reference_spacing` semantics for `pack_pages`) hasn't changed, only its internal measurement. If any of those tests fail, do not adjust them — stop and report the specific failure; it means an assumption in this plan about the existing pipeline was wrong.

- [ ] **Step 8: Commit**

```bash
git add scripts/assemble_sheet_music.py tests/test_normalize_scales.py
git commit -m "Normalize whole-set scale by staff width instead of staff spacing"
```

---

### Task 2: End-to-end coverage and `CLAUDE.md` §d

**Files:**
- Modify: `tests/test_cli_end_to_end.py` — add one new test
- Modify: `CLAUDE.md` §d (gitignored — see note below)

**Interfaces:** None new. Consumes `assemble`, `discover_pieces`, `load_rgb`, `strip_edge_border_lines`, `measure_content_width`, `normalize_piece_scales` (all existing after Task 1).

- [ ] **Step 1: Add the differing-width end-to-end test**

Today's `_snippet()` fixture in `tests/test_cli_end_to_end.py` is always called with the same `width` across every piece in a given test, so this exact bug was previously untestable end-to-end. Append this test to `tests/test_cli_end_to_end.py`:

```python
def test_different_width_pieces_normalize_to_matching_width(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # Two pieces at very different raw widths (e.g. different screenshot
    # resolutions or staff structures) should end up with matching staff
    # width after normalization -- not just matching note size.
    Image.fromarray(_snippet(width=800)).save(src / "Song_1.png")
    Image.fromarray(_snippet(width=1600)).save(src / "Song_2.png")

    summary = assemble(src, "Song", out)

    assert summary["pdf"].exists()
    assert any("to match staff width" in w for w in summary["warnings"])

    # Confirm the widths actually converge: re-run the same normalization
    # step the pipeline used, directly on the same source files.
    from assemble_sheet_music import (
        discover_pieces,
        load_rgb,
        measure_content_width,
        normalize_piece_scales,
        strip_edge_border_lines,
    )

    paths = discover_pieces(src, "Song")
    pieces = [strip_edge_border_lines(load_rgb(p))[0] for p in paths]
    normalized, _warnings, _reference = normalize_piece_scales(pieces)
    widths = [measure_content_width(p) for p in normalized]
    assert abs(widths[0] - widths[1]) <= 2
```

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/test_cli_end_to_end.py -v`
Expected: all pass, including the new test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Rewrite `CLAUDE.md` §d**

`CLAUDE.md` is gitignored in this repo (kept on disk, untracked — do not attempt to `git add` or un-ignore it; this is deliberate project convention). Edit the on-disk file directly. Find the section `**d. Whole-set staff-spacing normalization (always on)**` and replace it entirely with:

```markdown
**d. Whole-set staff-width normalization (always on)**
- Runs on *every* assembly, not just the insert case: before stacking,
  `normalize_piece_scales()` measures each piece's content WIDTH (the
  tightest bounding box around its ink, the same technique
  `crop_to_content` uses for its vertical extent) via
  `measure_content_width`, and rescales every piece to the **median**
  measured width — so pieces captured at different resolutions, or with
  different staff structures (e.g. a piano-only excerpt vs. a piano+vocal
  system), still stack into pages with visually consistent staff length.
  This matches how printed/engraved sheet music actually looks: every
  system on a page is the same width, and note spacing stretches or
  compresses slightly to fill it.
- Median (not mean/first) is the reference so a single oddly-scaled
  outlier (e.g. a title-block page) can't drag everything.
- A piece whose width can't be measured (no detectable ink — a blank
  piece) is left at native scale and a warning is emitted. If fewer than
  2 pieces are measurable there's no reliable reference and all pieces
  are left untouched.
- `SCALE_NOOP_TOLERANCE` (0.02): a piece already within 2% of the
  reference width is returned as the *same object*, unresampled — so an
  already-consistent set stays byte-for-byte identical to the
  pre-normalization output.
- The rescale is always isotropic (same factor both axes) — note shapes
  are never distorted, only their overall size.
- `normalize_piece_scales` still returns a third value, `reference_spacing`
  (used by the height-aware page packer, §b) — but it is no longer the
  controlled quantity. It's the median staff-line spacing *observed* on
  the already width-rescaled pieces: a representative sample of this run's
  note size, not something every piece is forced to match. It's `None`
  when fewer than 2 of the rescaled pieces have measurable spacing.
- **Known inconsistency, not yet solved:** the `--insert` feature (§c)
  still matches its inserted piece's *spacing* to `piece_arrays[0]`, which
  is no longer a controlled quantity post-width-normalization. An inserted
  piece may not end up width-consistent with the rest of the set. Flagged
  as a follow-up.
- Runs before the insert branch, so an inserted piece (which matches
  itself to `piece_arrays[0]`) targets the already-normalized set.
```

- [ ] **Step 5: Verify the on-disk file**

Read `CLAUDE.md` back and confirm §d reads correctly and `**c. Optional insert` (immediately before it) and `**e. Stray-mark cleanup**` (immediately after it) are untouched.

- [ ] **Step 6: Commit**

```bash
git add tests/test_cli_end_to_end.py
git commit -m "Add end-to-end coverage for differing-width normalization"
```

(`CLAUDE.md` is gitignored — its edit is expected to not appear in `git status` and is not part of this commit.)

---

## Self-Review

**Spec coverage:**
- `measure_content_width` — Task 1 Step 3. ✅
- `normalize_piece_scales` rescale-by-width, median reference, `SCALE_NOOP_TOLERANCE`/identity preservation, blank/unmeasurable handling, fewer-than-2 no-op — Task 1 Step 5. ✅
- `reference_spacing` as a post-rescale observation (not control), `None` fallback — Task 1 Step 5, tested in Task 1 Step 1's last two tests. ✅
- `pack_pages` / `sparse_page_warnings` require no changes — verified: neither is touched by any step in this plan. ✅
- `--insert` explicitly out of scope — not touched by any step; called out again in the `CLAUDE.md` rewrite (Task 2 Step 4) as a known follow-up. ✅
- Full test-suite replacement (not additive) for `tests/test_normalize_scales.py` — Task 1 Step 1 replaces the entire file. ✅
- New end-to-end differing-width test — Task 2 Step 1. ✅
- `CLAUDE.md` §d rewrite — Task 2 Step 4. ✅

**Placeholder scan:** No TODOs, no unfilled test bodies, every step has complete code. ✅

**Type consistency:** `measure_content_width(rgb: np.ndarray) -> int` used identically in Task 1 Step 3's definition, Task 1's test file, and Task 2's new test. `normalize_piece_scales`'s signature is unchanged from what Tasks 1-2 of the prior (page-packing) plan already established and `pack_pages` already consumes — verified no call site elsewhere needs updating. ✅
