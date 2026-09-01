"""Tests for the pre-allocation memory budget guard (#3).

The guard estimates input megapixels from file headers (no full decode) and
refuses to start an assembly that would blow the instance's memory, so an
oversized job returns a clean error instead of OOM-killing the process.
"""

import numpy as np
import pymupdf
import pytest
from PIL import Image

from assemble_sheet_music import (
    MemoryBudgetError,
    assemble,
    estimate_megapixels,
)


def _snippet(width=800, spacing=30, n_lines=5):
    top, thick, pad = 40, 3, 40
    height = top + spacing * (n_lines - 1) + thick + pad
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + thick, 30 : width - 30] = 0
    return img


def _write_pieces(dir_path, prefix, n):
    dir_path.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        Image.fromarray(_snippet()).save(dir_path / f"{prefix}_{i}.png")


def test_estimate_sums_raster_pixels_from_headers(tmp_path):
    Image.new("RGB", (600, 400)).save(tmp_path / "A.png")  # 0.24 MP
    Image.new("RGB", (1000, 500)).save(tmp_path / "B.jpg")  # 0.50 MP
    total = estimate_megapixels([tmp_path / "A.png", tmp_path / "B.jpg"])
    assert total == pytest.approx(0.74, rel=1e-3)


def test_estimate_counts_pdf_at_render_dpi(tmp_path):
    # a 72pt square renders to 300x300 px at 300 DPI -> 0.09 MP
    doc = pymupdf.open()
    doc.new_page(width=72, height=72)
    doc.save(tmp_path / "C.pdf")
    doc.close()
    total = estimate_megapixels([tmp_path / "C.pdf"])
    assert total == pytest.approx(0.09, rel=1e-2)


def test_assemble_rejects_over_budget(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    _write_pieces(src, "Song", 5)
    with pytest.raises(MemoryBudgetError) as exc:
        assemble(src, "Song", out, max_megapixels=0.001)
    assert "0.001" in str(exc.value) or "exceed" in str(exc.value).lower()


def test_assemble_within_budget_succeeds(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    _write_pieces(src, "Song", 5)
    summary = assemble(src, "Song", out, max_megapixels=1000.0)
    assert summary["pdf"].exists()


def test_no_budget_by_default_does_not_guard(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    _write_pieces(src, "Song", 5)
    # default max_megapixels=None -> no guard, CLI behaviour unchanged
    summary = assemble(src, "Song", out)
    assert summary["pdf"].exists()
