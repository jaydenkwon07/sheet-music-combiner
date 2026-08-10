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
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

MAX_PER_PAGE = 6
MIN_PER_PAGE = 4

# A pixel counts as "ink" when its grayscale value is below this. Threshold on
# the grayscale mean (not pure black) so colored logos/watermarks register too.
DARK_CUTOFF = 128
# A row is a staff line when this fraction of its pixels are ink (full-width
# horizontal line), distinguishing it from vertical barlines/stubs.
STAFF_ROW_FRACTION = 0.5

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


class DiscoveryError(Exception):
    """Raised when the numbered piece sequence is missing, gapped, or duplicated."""


class PageBalanceError(Exception):
    """Raised when N cannot be balanced within the min/max per-page bounds.

    The only such value is N=7. Callers should either present the options to
    a human or pass an explicit --pages override.
    """


def discover_pieces(input_dir: Path | str, prefix: str) -> list[Path]:
    """Find ``{prefix}_{n}.png`` files and validate a gap-free 1-based sequence.

    Filenames are NFC-normalized before matching so decomposed (NFD) Unicode
    uploads still match an NFC prefix. Returns the paths sorted by ``n``.
    Raises ``DiscoveryError`` with a specific message on any problem.
    """
    input_dir = Path(input_dir)
    prefix_nfc = unicodedata.normalize("NFC", prefix)
    pattern = re.compile(rf"^{re.escape(prefix_nfc)}_(\d+)\.png$", re.IGNORECASE)

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
            f"No files matching {prefix!r}_<n>.png found in {input_dir}"
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


def rescale_factor(reference_spacing: float, inserted_spacing: float) -> float:
    """Scale to apply to an inserted image so its staff spacing matches the
    reference: reference / inserted."""
    if inserted_spacing <= 0:
        raise ValueError("inserted_spacing must be positive")
    return reference_spacing / inserted_spacing


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


# --------------------------------------------------------------------------
# Image I/O and rendering (thin wrappers over PIL; pure logic lives above).
# --------------------------------------------------------------------------


def load_rgb(path: Path | str) -> np.ndarray:
    """Load an image as an (H, W, 3) uint8 RGB array."""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


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
    """Combine rendered letter pages into a single RGB, 300-DPI PDF."""
    images = [Image.fromarray(p).convert("RGB") for p in pages]
    images[0].save(
        path,
        format="PDF",
        resolution=float(DPI),
        save_all=True,
        append_images=images[1:],
    )


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
) -> dict:
    """Full deterministic pipeline. Returns a summary dict and writes outputs.

    Raises DiscoveryError / PageBalanceError / ValueError with clear messages;
    the CLI turns those into non-zero exits rather than guessing.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix_nfc = unicodedata.normalize("NFC", prefix)

    paths = discover_pieces(input_dir, prefix)
    warnings: list[str] = []
    insert_factor: float | None = None

    piece_arrays = []
    for i, p in enumerate(paths, start=1):
        cleaned, removed = strip_edge_border_lines(load_rgb(p))
        piece_arrays.append(cleaned)
        for r in removed:
            warnings.append(f"piece {i} ({p.name}): {r}")

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
            pre_layout = balance_pages(len(paths))  # map against current layout
            flat_index = flat_index_for_position(
                pre_layout, int(page_str), int(idx_str)
            )
        piece_arrays.insert(flat_index, ins_scaled)
        warnings.append(f"inserted piece rescaled by {insert_factor:.3f} at flat index {flat_index}")

    total = len(piece_arrays)
    if pages_spec is not None:
        counts = parse_pages_override(pages_spec, total)
    else:
        counts = balance_pages(total)  # may raise PageBalanceError (N=7 gap)

    grouped = _split_into_pages(piece_arrays, counts)
    page_images: list[np.ndarray] = []
    for i, group in enumerate(grouped, start=1):
        stacked = stack_pieces(group)
        cleaned, removed = clean_stray_marks(stacked)
        cropped = crop_to_content(cleaned)
        page_images.append(cropped)
        if removed:
            warnings.append(f"page {i}: removed {len(removed)} stray group(s) [{'; '.join(removed)}]")

    page_sizes = [(p.shape[1], p.shape[0]) for p in page_images]
    scale = compute_uniform_scale(page_sizes, margin)

    png_paths: list[Path] = []
    rendered: list[np.ndarray] = []
    for i, page in enumerate(page_images, start=1):
        letter = render_letter_page(page, scale, margin)
        rendered.append(letter)
        png_path = output_dir / f"{prefix_nfc}_page{i}.png"
        save_png(letter, png_path)
        png_paths.append(png_path)

    pdf_path = output_dir / f"{prefix_nfc}.pdf"
    save_pdf(rendered, pdf_path)

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
        )
    except PageBalanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (DiscoveryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
