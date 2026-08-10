"""Tests for per-snippet edge border-line stripping.

Covers the case clean_stray_marks can't: a solid screenshot/crop-edge line
that runs the full height (or width) of a *snippet*, discovered on a real
re-exported piece. Once systems are stacked into a page, such a line only
spans one system's row-band, so it no longer reads as an isolated group at
the page level -- it has to be caught per snippet, before stacking.
"""

import numpy as np

from assemble_sheet_music import strip_edge_border_lines


def _white(h, w):
    return np.full((h, w, 3), 255, dtype=np.uint8)


def _main_block(img):
    """Central content block spanning the full height, like a real system."""
    img[:, 60:341] = 0
    return img


def test_removes_solid_left_edge_line_full_height():
    img = _main_block(_white(300, 400))
    img[:, 0:2] = 0  # solid 2px line at the true left edge, full height

    out, removed = strip_edge_border_lines(img)

    assert removed
    assert (out[:, 0:2] == 255).all(), "border line should be whitened"
    assert (out[:, 60:341] == 0).all(), "main content must be untouched"


def test_removes_solid_right_edge_line():
    img = _main_block(_white(300, 400))
    img[:, 396:400] = 0  # solid 4px line at the true right edge

    out, removed = strip_edge_border_lines(img)

    assert removed
    assert (out[:, 396:400] == 255).all()
    assert (out[:, 60:341] == 0).all()


def test_removes_solid_top_and_bottom_edge_lines():
    img = _white(300, 400)
    img[:, 60:341] = 0
    img[0:1, :] = 0
    img[299:300, :] = 0

    out, removed = strip_edge_border_lines(img)

    assert len(removed) == 2
    assert (out[0:1, :] == 255).all()
    assert (out[299:300, :] == 255).all()


def test_does_not_touch_brace_like_partial_density_column():
    """A real brace/bracket is thick in the middle, thin at the tips -- never
    solid for its whole length -- so it must survive untouched."""
    img = _main_block(_white(300, 400))
    # Column 3 is dark for only part of the height (like a curved brace tip),
    # well under the solid-line threshold.
    img[100:200, 3] = 0

    out, removed = strip_edge_border_lines(img)

    assert removed == []
    assert (out[100:200, 3] == 0).all(), "partial-density content must survive"


def test_no_ink_returns_unchanged():
    img = _white(120, 120)
    out, removed = strip_edge_border_lines(img)
    assert removed == []
    assert (out == 255).all()


def test_clean_image_with_normal_content_untouched():
    img = _main_block(_white(300, 400))
    out, removed = strip_edge_border_lines(img)
    assert removed == []
    assert (out == img).all()
