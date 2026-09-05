#!/usr/bin/env python3
"""Assemble numbered sheet-music snippet images into paginated US-Letter PDFs.

Deterministic image-processing CLI. No LLM calls: identical inputs always
produce identical output. See CLAUDE.md for the full specification.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

MAX_PER_PAGE = 6
MIN_PER_PAGE = 4

# A pixel counts as "ink" when its grayscale value is below this. Threshold on
# the grayscale mean (not pure black) so colored logos/watermarks register too.
# Kept well below 255 (not e.g. ~250) because real notation-export backgrounds
# measure exactly 255 with no compression noise, but a thin vector line (a
# closing/measure barline especially) anti-aliases to a genuinely faint gray --
# measured as light as ~230 in real exports -- that must still count as content
# or crop_to_content treats it as background and crops it away (every system's
# trailing barline getting cut off is exactly this bug).
DARK_CUTOFF = 235
# A row is a staff line when this fraction of its pixels are ink (full-width
# horizontal line), distinguishing it from vertical barlines/stubs.
STAFF_ROW_FRACTION = 0.5
# When normalizing every piece to a common staff width, a rescale within this
# fraction of 1.0 is skipped -- avoids needlessly resampling (softening) pieces
# that already match, and keeps an already-consistent set byte-for-byte
# identical to the pre-normalization output.
SCALE_NOOP_TOLERANCE = 0.02
# A width-rescale factor outside this band implies a piece whose measured content
# width is implausible (a near-blank page measuring a few px, or a corrupt
# capture) -- applying it would balloon the piece to an enormous array. The web
# memory guard estimates size from file headers BEFORE load, so it can't see a
# post-load rescale; such a piece is left at native scale and warned, like an
# unmeasurable one.
WIDTH_RESCALE_MIN_FACTOR = 0.2
WIDTH_RESCALE_MAX_FACTOR = 5.0

# Gap-tolerant grouping of connected ink when finding stray-mark groups.
GROUP_GAP_PX = 25
# A non-main ink group is a stray only if it hugs an edge: within this many
# pixels of the left/right edge, or of the top/bottom edge.
EDGE_LR_PX = 90
EDGE_TB_PX = 45
# Top/bottom strays must also be thin, so we never erase a real short system.
THIN_ROW_MAX_PX = 18

# Per-snippet (pre-stack) border-line detection: a leftover screenshot/crop
# edge shows up as a column or row, right at the true image edge, that is
# almost solid ink for its entire length. Checked within this many pixels of
# each edge...
BORDER_SCAN_PX = 8
# ...and only whitened if its dark-pixel fraction clears this bar. Calibrated
# against real scans: the densest real notation (braces, clefs) tops out
# around 0.6 even at its thickest; only a synthetic border stripe is this
# solid, so this is safe to catch even when it overlaps other content's span
# (unlike clean_stray_marks, which only fires on marks isolated from content).
BORDER_LINE_FRACTION = 0.97

# US Letter at 300 DPI.
LETTER_WIDTH_PX = 2550
LETTER_HEIGHT_PX = 3300
DEFAULT_MARGIN_PX = 22
DPI = 300
# Vertical whitespace inserted between stacked systems on a page.
STACK_GAP_PX = 40

# Target fraction of the usable page height a packed page fills. Below 1.0 so
# systems get engraving-like breathing room top and bottom instead of being
# crammed to the page edge; pack_pages uses it to size the per-page budget.
PAGE_FILL_FRACTION = 0.9


class DiscoveryError(Exception):
    """Raised when the numbered piece sequence is missing, gapped, or duplicated."""


class PageBalanceError(Exception):
    """Raised when N cannot be balanced within the min/max per-page bounds.

    The only such value is N=7. Callers should either present the options to
    a human or pass an explicit --pages override.
    """


class MemoryBudgetError(Exception):
    """Raised before allocating when the estimated input size exceeds the
    configured megapixel budget. Lets a caller (e.g. the memory-limited web
    instance) reject an oversized job cleanly instead of being OOM-killed
    part way through assembly.
    """


def discover_pieces(input_dir: Path | str, prefix: str) -> list[Path]:
    """Find ``{prefix}_{n}.{png,jpg,jpeg,pdf}`` files and validate a gap-free
    1-based sequence.

    Filenames are NFC-normalized before matching so decomposed (NFD) Unicode
    uploads still match an NFC prefix. Returns the paths sorted by ``n``.
    Raises ``DiscoveryError`` with a specific message on any problem.
    """
    input_dir = Path(input_dir)
    prefix_nfc = unicodedata.normalize("NFC", prefix)
    pattern = re.compile(
        rf"^{re.escape(prefix_nfc)}_(\d+)\.(?:png|jpe?g|pdf)$", re.IGNORECASE
    )

    found: dict[int, list[Path]] = {}
    for entry in input_dir.iterdir():
        if not entry.is_file():
            continue
        name_nfc = unicodedata.normalize("NFC", entry.name)
        match = pattern.match(name_nfc)
        if match:
            found.setdefault(int(match.group(1)), []).append(entry)

    if not found:
        raise DiscoveryError(
            f"No files matching {prefix!r}_<n>.(png|jpg|jpeg|pdf) found in {input_dir}"
        )

    duplicates = {n: paths for n, paths in found.items() if len(paths) > 1}
    if duplicates:
        detail = "; ".join(
            f"n={n}: {sorted(p.name for p in paths)}" for n, paths in sorted(duplicates.items())
        )
        raise DiscoveryError(f"Duplicate piece number(s): {detail}")

    numbers = sorted(found)
    expected = list(range(1, max(numbers) + 1))
    missing = sorted(set(expected) - set(numbers))
    if numbers[0] != 1 or missing:
        want_missing = missing or [1]
        raise DiscoveryError(
            f"Sequence must be contiguous starting at 1. Missing: {want_missing}. "
            f"Found: {numbers}"
        )

    return [found[n][0] for n in numbers]


def balance_pages(n: int) -> list[int]:
    """Distribute N pieces across pages, 4-6 per page, spread of at most 1.

    num_pages = ceil(N/6); base = N // num_pages; remainder = N % num_pages.
    The `remainder` pages get base+1 (and lead), the rest get base.
    """
    if n < 1:
        raise ValueError(f"N must be >= 1, got {n}")
    num_pages = math.ceil(n / MAX_PER_PAGE)
    base = n // num_pages
    remainder = n % num_pages
    pages = [base + 1] * remainder + [base] * (num_pages - remainder)
    if any(p < MIN_PER_PAGE or p > MAX_PER_PAGE for p in pages):
        raise PageBalanceError(
            f"N={n} cannot be balanced within {MIN_PER_PAGE}-{MAX_PER_PAGE} "
            f"pieces per page. Options: 1 page of {n} (over max {MAX_PER_PAGE}), "
            f"or {num_pages} pages {pages} (breaks min {MIN_PER_PAGE}). "
            f"Pass --pages to choose explicitly."
        )
    return pages


def parse_pages_override(spec: str, total: int) -> list[int]:
    """Parse an explicit ``--pages "5,5,4"`` override and check it sums to N.

    The override is authoritative on per-page distribution (it may fall outside
    the 4-6 band, e.g. to resolve the N=7 gap), but it must account for every
    piece exactly once.
    """
    try:
        pages = [int(part.strip()) for part in spec.split(",")]
    except ValueError as exc:
        raise ValueError(f"--pages must be a comma list of integers, got {spec!r}") from exc
    if any(p <= 0 for p in pages):
        raise ValueError(f"--pages entries must be positive, got {pages}")
    if sum(pages) != total:
        raise ValueError(
            f"--pages {pages} sums to {sum(pages)} but there are {total} pieces"
        )
    return pages


def _group_consecutive(indices: np.ndarray, gap: int = 1) -> list[tuple[int, int]]:
    """Group sorted integer indices into (start, end) runs, merging across gaps
    up to ``gap`` pixels. ``end`` is inclusive."""
    if len(indices) == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(indices[0])
    for idx in indices[1:]:
        idx = int(idx)
        if idx - prev <= gap:
            prev = idx
        else:
            runs.append((start, prev))
            start = prev = idx
    runs.append((start, prev))
    return runs


def measure_staff_spacing(gray: np.ndarray) -> float:
    """Median vertical gap between staff lines in a grayscale image array.

    1. dark-pixel fraction per row; 2. rows above STAFF_ROW_FRACTION are staff
    lines; 3. group consecutive such rows into line centers; 4. return the
    median gap between adjacent centers.
    """
    dark = gray < DARK_CUTOFF
    row_fraction = dark.mean(axis=1)
    staff_rows = np.flatnonzero(row_fraction > STAFF_ROW_FRACTION)
    runs = _group_consecutive(staff_rows, gap=1)
    if len(runs) < 2:
        raise ValueError(
            f"Need at least 2 staff lines to measure spacing, found {len(runs)}"
        )
    centers = np.array([(start + end) / 2 for start, end in runs])
    gaps = np.diff(centers)
    return float(np.median(gaps))


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


def rescale_factor(reference_spacing: float, inserted_spacing: float) -> float:
    """Scale to apply to an inserted image so its staff spacing matches the
    reference: reference / inserted."""
    if inserted_spacing <= 0:
        raise ValueError("inserted_spacing must be positive")
    return reference_spacing / inserted_spacing


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
    width). ``reference_width`` is the median measured content width (the same
    value the pieces were rescaled toward), which pack_pages uses to size its
    physical page-height budget; it is None when fewer than 2 pieces had
    measurable width. Pieces already within SCALE_NOOP_TOLERANCE of the width
    reference are returned unchanged (same object), so an already-consistent set
    is a true no-op.
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
        if not (WIDTH_RESCALE_MIN_FACTOR <= factor <= WIDTH_RESCALE_MAX_FACTOR):
            out.append(piece)
            warnings.append(
                f"piece {i}: implied width rescale x{factor:.3f} is outside the "
                f"[{WIDTH_RESCALE_MIN_FACTOR}, {WIDTH_RESCALE_MAX_FACTOR}] safe band "
                f"(likely a near-blank or corrupt piece); left at native scale"
            )
            continue
        out.append(resize_rgb(piece, factor))
        warnings.append(f"piece {i}: rescaled x{factor:.3f} to match staff width")

    return out, warnings, reference_width


def _to_ink_mask(arr: np.ndarray) -> np.ndarray:
    """Boolean mask, True where a pixel is ink (dark or a colored logo)."""
    if arr.ndim == 2:
        gray = arr.astype(np.float32)
    else:
        gray = arr[..., :3].mean(axis=2)
    return gray < DARK_CUTOFF


def find_ink_groups(mask: np.ndarray, axis: str, gap: int = GROUP_GAP_PX) -> list[tuple[int, int, int]]:
    """Group connected ink along one axis into (start, end, ink_total) runs.

    axis="x" gives column-groups (horizontal spans); axis="y" gives row-groups.
    """
    if axis == "x":
        present = np.flatnonzero(mask.any(axis=0))
        runs = _group_consecutive(present, gap)
        return [(s, e, int(mask[:, s : e + 1].sum())) for s, e in runs]
    if axis == "y":
        present = np.flatnonzero(mask.any(axis=1))
        runs = _group_consecutive(present, gap)
        return [(s, e, int(mask[s : e + 1, :].sum())) for s, e in runs]
    raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")


def clean_stray_marks(
    rgb: np.ndarray,
    edge_lr: int = EDGE_LR_PX,
    edge_tb: int = EDGE_TB_PX,
) -> tuple[np.ndarray, list[str]]:
    """Whiten isolated, edge-touching, out-of-band ink groups.

    Never modifies pixels inside the main content region (the intersection of
    the largest column-group's x-span and the largest row-group's y-span):
    every whitened group lies outside the main group's span on its own axis.
    Returns (cleaned_copy, human-readable descriptions of what was removed).
    """
    arr = np.asarray(rgb)
    mask = _to_ink_mask(arr)
    height, width = mask.shape
    out = arr.copy()
    removed: list[str] = []

    col_groups = find_ink_groups(mask, "x")
    row_groups = find_ink_groups(mask, "y")
    if not col_groups:  # blank image, nothing to do
        return out, removed

    main_col = max(col_groups, key=lambda g: g[2])
    main_row = max(row_groups, key=lambda g: g[2])

    for x0, x1, _ink in col_groups:
        if (x0, x1) == main_col[:2]:
            continue
        near_edge = x0 <= edge_lr or x1 >= width - 1 - edge_lr
        overlaps_main = not (x1 < main_col[0] or x0 > main_col[1])
        if near_edge and not overlaps_main:
            out[:, x0 : x1 + 1] = 255
            removed.append(f"left/right stray columns {x0}-{x1}")

    for y0, y1, _ink in row_groups:
        if (y0, y1) == main_row[:2]:
            continue
        thin = (y1 - y0 + 1) < THIN_ROW_MAX_PX
        near_edge = y0 <= edge_tb or y1 >= height - 1 - edge_tb
        overlaps_main = not (y1 < main_row[0] or y0 > main_row[1])
        if near_edge and thin and not overlaps_main:
            out[y0 : y1 + 1, :] = 255
            removed.append(f"top/bottom stray rows {y0}-{y1}")

    return out, removed


def strip_edge_border_lines(
    rgb: np.ndarray, scan: int = BORDER_SCAN_PX, threshold: float = BORDER_LINE_FRACTION
) -> tuple[np.ndarray, list[str]]:
    """Whiten thin, near-solid lines hugging the true edge of a single snippet.

    Catches leftover screenshot/crop borders (e.g. a re-exported snippet that
    carries a 1-4px solid stripe along one edge) that ``clean_stray_marks``
    can't see once systems are stacked into a page -- at that point the line
    only spans one system's row-band, not the whole page, so it no longer
    reads as an isolated top/bottom-edge group there. Run this per snippet,
    before stacking, where "solid for its whole length, at the true edge" is
    unambiguous. Only whitens the specific offending column(s)/row(s), never
    a whole margin band.
    """
    arr = np.asarray(rgb)
    mask = _to_ink_mask(arr)
    height, width = mask.shape
    out = arr.copy()
    removed: list[str] = []

    left_frac = mask[:, :scan].mean(axis=0)
    for x in np.flatnonzero(left_frac > threshold):
        out[:, x] = 255
        removed.append(f"left border line at col {x}")

    right_frac = mask[:, width - scan :].mean(axis=0)
    for offset in np.flatnonzero(right_frac > threshold):
        x = width - scan + offset
        out[:, x] = 255
        removed.append(f"right border line at col {x}")

    top_frac = mask[:scan, :].mean(axis=1)
    for y in np.flatnonzero(top_frac > threshold):
        out[y, :] = 255
        removed.append(f"top border line at row {y}")

    bottom_frac = mask[height - scan :, :].mean(axis=1)
    for offset in np.flatnonzero(bottom_frac > threshold):
        y = height - scan + offset
        out[y, :] = 255
        removed.append(f"bottom border line at row {y}")

    return out, removed


def crop_to_content(rgb: np.ndarray) -> np.ndarray:
    """Crop tightly to the bounding box of remaining ink. Blank -> unchanged."""
    arr = np.asarray(rgb)
    mask = _to_ink_mask(arr)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if len(rows) == 0 or len(cols) == 0:
        return arr
    return arr[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]


def compute_uniform_scale(
    page_sizes: list[tuple[int, int]], margin: int = DEFAULT_MARGIN_PX
) -> float:
    """One scale factor for ALL pages, so notation is the same physical size
    everywhere. Fits the largest page width/height into the usable letter area.
    """
    if not page_sizes:
        raise ValueError("page_sizes must be non-empty")
    max_w = max(w for w, _ in page_sizes)
    max_h = max(h for _, h in page_sizes)
    usable_w = LETTER_WIDTH_PX - 2 * margin
    usable_h = LETTER_HEIGHT_PX - 2 * margin
    return min(usable_w / max_w, usable_h / max_h)


def flat_index_for_position(pages: list[int], page: int, index: int) -> int:
    """Translate a 1-based page and 0-based within-page index (against the
    current layout) into a flat index in the ordered piece sequence."""
    if page < 1 or page > len(pages):
        raise ValueError(f"page {page} out of range 1..{len(pages)}")
    if index < 0 or index >= pages[page - 1]:
        raise ValueError(f"index {index} out of range for page {page} (size {pages[page - 1]})")
    return sum(pages[: page - 1]) + index


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


def _group_height(heights: list[int], gap: int) -> float:
    """Stacked height of a contiguous run of pieces: their heights plus the
    gaps stack_pieces will insert between them."""
    if not heights:
        return 0.0
    return sum(heights) + (len(heights) - 1) * gap


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


# --------------------------------------------------------------------------
# Image I/O and rendering (thin wrappers over PIL; pure logic lives above).
# --------------------------------------------------------------------------


def load_rgb(path: Path | str) -> np.ndarray:
    """Load a piece as an (H, W, 3) uint8 RGB array.

    Raster formats (PNG/JPG) go through Pillow. A PDF piece is one snippet:
    its first page is rendered at the module DPI (extra pages are ignored here;
    ``assemble`` warns about them via ``pdf_page_count``).
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            pix = doc[0].get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        return np.ascontiguousarray(arr.reshape(pix.height, pix.width, 3))
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def pdf_page_count(path: Path | str) -> int:
    """Number of pages in a PDF (used to warn when a PDF piece has extras)."""
    with pymupdf.open(path) as doc:
        return doc.page_count


def _piece_pixel_dims(path: Path) -> tuple[int, int]:
    """(width, height) in pixels a piece will occupy once loaded, read cheaply
    from headers/metadata -- never decoding or rendering the full image."""
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            rect = doc[0].rect  # first page only; that's the piece we render
        return round(rect.width * DPI / 72), round(rect.height * DPI / 72)
    with Image.open(path) as im:  # PIL reads size from the header lazily
        return im.width, im.height


def estimate_megapixels(paths: list[Path]) -> float:
    """Total megapixels the given pieces will occupy in memory, estimated from
    headers without decoding. Used by the pre-allocation memory guard."""
    return sum(w * h for w, h in (_piece_pixel_dims(Path(p)) for p in paths)) / 1e6


def resize_rgb(arr: np.ndarray, scale: float) -> np.ndarray:
    """Resize an RGB array by a scale factor (LANCZOS)."""
    if scale == 1.0:
        return arr
    h, w = arr.shape[:2]
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    im = Image.fromarray(arr).resize(new_size, Image.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def stack_pieces(pieces: list[np.ndarray], gap: int = STACK_GAP_PX) -> np.ndarray:
    """Crop each piece to its content and stack them vertically, centered
    horizontally on a white canvas, with `gap` pixels between systems."""
    cropped = [crop_to_content(p) for p in pieces]
    canvas_w = max(c.shape[1] for c in cropped)
    total_h = sum(c.shape[0] for c in cropped) + gap * (len(cropped) - 1)
    canvas = np.full((total_h, canvas_w, 3), 255, dtype=np.uint8)
    y = 0
    for c in cropped:
        h, w = c.shape[:2]
        x = (canvas_w - w) // 2
        canvas[y : y + h, x : x + w] = c
        y += h + gap
    return canvas


def render_letter_page(page: np.ndarray, scale: float, margin: int = DEFAULT_MARGIN_PX) -> np.ndarray:
    """Scale a page image and paste it centered on a US-Letter white canvas."""
    scaled = resize_rgb(page, scale)
    canvas = np.full((LETTER_HEIGHT_PX, LETTER_WIDTH_PX, 3), 255, dtype=np.uint8)
    h, w = scaled.shape[:2]
    x = (LETTER_WIDTH_PX - w) // 2
    y = (LETTER_HEIGHT_PX - h) // 2
    canvas[y : y + h, x : x + w] = scaled
    return canvas


def save_pdf(pages: list[np.ndarray], path: Path | str) -> None:
    """Combine rendered letter pages into a single RGB, 300-DPI PDF.

    Pins creationDate/modDate instead of leaving PIL's default of "now" --
    otherwise the PDF's bytes (though never its rendered content) would
    differ between two runs on identical inputs, breaking determinism.
    """
    images = [Image.fromarray(p).convert("RGB") for p in pages]
    epoch = time.gmtime(0)
    images[0].save(
        path,
        format="PDF",
        resolution=float(DPI),
        save_all=True,
        append_images=images[1:],
        creationDate=epoch,
        modDate=epoch,
    )


def save_pdf_from_paths(png_paths: list[Path | str], path: Path | str) -> None:
    """Combine already-written page PNGs into one RGB, 300-DPI PDF.

    Opens each PNG lazily instead of taking full-resolution page arrays in
    memory, so peak RAM during PDF assembly stays near one page rather than
    all of them -- the difference between fitting in a 512 MB instance and
    being OOM-killed. Bytes match ``save_pdf`` for identical pixels; dates are
    pinned to the epoch so identical inputs stay byte-identical.
    """
    images = [Image.open(p) for p in png_paths]
    try:
        epoch = time.gmtime(0)
        images[0].save(
            path,
            format="PDF",
            resolution=float(DPI),
            save_all=True,
            append_images=images[1:],
            creationDate=epoch,
            modDate=epoch,
        )
    finally:
        for im in images:
            im.close()


def save_png(arr: np.ndarray, path: Path | str) -> None:
    Image.fromarray(arr).convert("RGB").save(path, format="PNG", dpi=(DPI, DPI))


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _split_into_pages(items: list, counts: list[int]) -> list[list]:
    pages, start = [], 0
    for count in counts:
        pages.append(items[start : start + count])
        start += count
    return pages


def assemble(
    input_dir: Path | str,
    prefix: str,
    output_dir: Path | str,
    pages_spec: str | None = None,
    insert: Path | str | None = None,
    at_top: bool = False,
    at_position: str | None = None,
    margin: int = DEFAULT_MARGIN_PX,
    max_megapixels: float | None = None,
) -> dict:
    """Full deterministic pipeline. Returns a summary dict and writes outputs.

    Raises DiscoveryError / PageBalanceError / ValueError with clear messages;
    the CLI turns those into non-zero exits rather than guessing. When
    ``max_megapixels`` is set, an over-budget input raises MemoryBudgetError
    before any large allocation (default None keeps the CLI unguarded).
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix_nfc = unicodedata.normalize("NFC", prefix)

    paths = discover_pieces(input_dir, prefix)

    if max_megapixels is not None:
        est = estimate_megapixels(paths + ([Path(insert)] if insert else []))
        if est > max_megapixels:
            raise MemoryBudgetError(
                f"Estimated input is ~{est:.1f} megapixels, which exceeds this "
                f"instance's {max_megapixels:.1f} MP limit. Send fewer/smaller "
                f"pieces, or run on an instance with more memory. (PDF pieces "
                f"count at 300 DPI, so they are far heavier than screenshots.)"
            )

    warnings: list[str] = []
    insert_factor: float | None = None

    piece_arrays = []
    for i, p in enumerate(paths, start=1):
        cleaned, removed = strip_edge_border_lines(load_rgb(p))
        piece_arrays.append(cleaned)
        for r in removed:
            warnings.append(f"piece {i} ({p.name}): {r}")
        if p.suffix.lower() == ".pdf" and (n_pages := pdf_page_count(p)) > 1:
            warnings.append(
                f"piece {i} ({p.name}): PDF has {n_pages} pages; used page 1 only"
            )

    # Normalize every piece to a common staff WIDTH before stacking, so pieces
    # captured at different resolutions or with different staff structures stack
    # into pages with consistent staff length (like real engraved sheet music).
    # reference_width feeds pack_pages's physical page-height budget. Done before
    # the insert branch so the inserted piece (which matches itself to
    # piece_arrays[0]) targets the normalized set.
    piece_arrays, norm_warnings, reference_width = normalize_piece_scales(piece_arrays)
    warnings.extend(norm_warnings)

    if insert is not None:
        if not at_top and at_position is None:
            raise ValueError("--insert requires --at-top or --at-position")
        ref_gray = piece_arrays[0][..., :3].mean(axis=2)
        ins_arr, ins_removed = strip_edge_border_lines(load_rgb(insert))
        for r in ins_removed:
            warnings.append(f"inserted piece: {r}")
        ins_gray = ins_arr[..., :3].mean(axis=2)
        insert_factor = rescale_factor(
            measure_staff_spacing(ref_gray), measure_staff_spacing(ins_gray)
        )
        ins_scaled = resize_rgb(ins_arr, insert_factor)

        if at_top:
            flat_index = 0
        else:
            page_str, _, idx_str = at_position.partition(":")
            pre_heights = [p.shape[0] for p in piece_arrays]
            pre_layout = pack_pages(pre_heights, reference_width, margin=margin)  # map against current layout
            flat_index = flat_index_for_position(
                pre_layout, int(page_str), int(idx_str)
            )
        piece_arrays.insert(flat_index, ins_scaled)
        warnings.append(f"inserted piece rescaled by {insert_factor:.3f} at flat index {flat_index}")

    total = len(piece_arrays)
    if pages_spec is not None:
        counts = parse_pages_override(pages_spec, total)
    else:
        piece_heights = [p.shape[0] for p in piece_arrays]
        counts = pack_pages(piece_heights, reference_width, margin=margin)
        if reference_width is not None:
            warnings.extend(
                sparse_page_warnings(piece_heights, counts, reference_width, margin=margin)
            )

    grouped = _split_into_pages(piece_arrays, counts)
    page_images: list[np.ndarray] = []
    for i, group in enumerate(grouped, start=1):
        stacked = stack_pieces(group)
        cleaned, removed = clean_stray_marks(stacked)
        cropped = crop_to_content(cleaned)
        page_images.append(cropped)
        if removed:
            warnings.append(f"page {i}: removed {len(removed)} stray group(s) [{'; '.join(removed)}]")

    # The per-piece arrays are fully consumed into page_images now; drop them so
    # their memory is reclaimed before the (larger) letter-page render phase.
    del piece_arrays, grouped

    page_sizes = [(p.shape[1], p.shape[0]) for p in page_images]
    scale = compute_uniform_scale(page_sizes, margin)

    # Render each page, write its PNG, and release the full-resolution array
    # immediately -- never hold every ~24 MB letter page in RAM at once. The PDF
    # is then streamed from the on-disk PNGs (see save_pdf_from_paths).
    png_paths: list[Path] = []
    for i in range(len(page_images)):
        letter = render_letter_page(page_images[i], scale, margin)
        page_images[i] = None  # type: ignore[assignment]  # free the source page
        png_path = output_dir / f"{prefix_nfc}_page{i + 1}.png"
        save_png(letter, png_path)
        del letter
        png_paths.append(png_path)

    pdf_path = output_dir / f"{prefix_nfc}.pdf"
    save_pdf_from_paths(png_paths, pdf_path)

    return {
        "prefix": prefix_nfc,
        "num_pieces": total,
        "counts": counts,
        "uniform_scale": scale,
        "insert_factor": insert_factor,
        "page_pngs": png_paths,
        "pdf": pdf_path,
        "warnings": warnings,
    }


def _print_summary(summary: dict) -> None:
    print(f"{summary['prefix']}: {summary['num_pieces']} pieces "
          f"-> {len(summary['counts'])} page(s) {summary['counts']}")
    print(f"uniform scale: {summary['uniform_scale']:.4f}")
    for w in summary["warnings"]:
        print(f"  warning: {w}")
    print(f"PDF: {summary['pdf']}")
    for p in summary["page_pngs"]:
        print(f"  page: {p}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Assemble numbered sheet-music snippets into US-Letter PDFs.")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--pages", default=None, help='explicit distribution, e.g. "5,5,4"')
    p.add_argument("--insert", default=None, help="path to an extra piece to add before layout")
    p.add_argument("--at-top", action="store_true", help="insert at the very top")
    p.add_argument("--at-position", default=None, help="insert at <page>:<index> of the current layout")
    p.add_argument("--margin", type=int, default=DEFAULT_MARGIN_PX)
    p.add_argument(
        "--max-megapixels",
        type=float,
        default=None,
        help="reject the job (before allocating) if estimated input exceeds this "
        "many megapixels; guards a memory-limited host. Unset = no limit.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = assemble(
            input_dir=args.input_dir,
            prefix=args.prefix,
            output_dir=args.output_dir,
            pages_spec=args.pages,
            insert=args.insert,
            at_top=args.at_top,
            at_position=args.at_position,
            margin=args.margin,
            max_megapixels=args.max_megapixels,
        )
    except PageBalanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (DiscoveryError, MemoryBudgetError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
