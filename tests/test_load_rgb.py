"""Tests for multi-format loading: JPG via Pillow, PDF via PyMuPDF."""

import numpy as np
import pymupdf
from PIL import Image

from assemble_sheet_music import DPI, load_rgb, pdf_page_count


def _make_pdf(path, pages=1, pts=72):
    """A `pts`-point-square PDF; at 300 DPI a page renders to `pts/72*300` px."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=pts, height=pts)
        page.draw_rect(pymupdf.Rect(10, 10, pts - 10, pts - 10), fill=(0, 0, 0))
    doc.save(path)
    doc.close()


def test_load_jpg_returns_rgb_array(tmp_path):
    p = tmp_path / "piece.jpg"
    Image.new("RGB", (40, 30), (200, 100, 50)).save(p, format="JPEG")
    arr = load_rgb(p)
    assert arr.shape == (30, 40, 3)
    assert arr.dtype == np.uint8


def test_load_pdf_renders_first_page_at_module_dpi(tmp_path):
    p = tmp_path / "piece.pdf"
    _make_pdf(p, pages=1, pts=72)  # 72 pt square -> 300x300 px at 300 DPI
    arr = load_rgb(p)
    expected = round(72 / 72 * DPI)
    assert arr.shape == (expected, expected, 3)
    assert arr.dtype == np.uint8
    # the drawn rectangle means the page is not all white
    assert arr.min() < 128


def test_pdf_page_count_reports_extra_pages(tmp_path):
    p = tmp_path / "multi.pdf"
    _make_pdf(p, pages=3)
    assert pdf_page_count(p) == 3


def test_load_pdf_uses_only_first_page(tmp_path):
    p = tmp_path / "multi.pdf"
    _make_pdf(p, pages=2, pts=72)
    arr = load_rgb(p)
    assert arr.shape == (DPI, DPI, 3)  # single page, not stacked
