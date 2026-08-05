"""Tests for uniform letter-page scaling."""

import pytest

from assemble_sheet_music import (
    LETTER_HEIGHT_PX,
    LETTER_WIDTH_PX,
    compute_uniform_scale,
)


def test_single_scale_uses_the_binding_dimension():
    # usable = 2550-44=2506 wide, 3300-44=3256 tall (margin 22 each side)
    scale = compute_uniform_scale([(1000, 2000), (1200, 1800)], margin=22)
    # max_w=1200 -> 2506/1200=2.088 ; max_h=2000 -> 3256/2000=1.628 -> min
    assert scale == pytest.approx(3256 / 2000, rel=1e-6)


def test_same_scale_regardless_of_which_page_is_widest():
    s = compute_uniform_scale([(2506, 100)], margin=22)
    assert s == pytest.approx(1.0, rel=1e-6)


def test_scaled_pages_fit_within_usable_area():
    pages = [(1500, 2400), (900, 3000), (2000, 1000)]
    margin = 22
    scale = compute_uniform_scale(pages, margin=margin)
    max_w = max(w for w, _ in pages)
    max_h = max(h for _, h in pages)
    assert max_w * scale <= LETTER_WIDTH_PX - 2 * margin + 1e-6
    assert max_h * scale <= LETTER_HEIGHT_PX - 2 * margin + 1e-6


def test_letter_dimensions_are_300dpi():
    assert (LETTER_WIDTH_PX, LETTER_HEIGHT_PX) == (2550, 3300)
