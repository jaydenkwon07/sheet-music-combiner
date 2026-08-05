"""Tests for stray-mark cleanup and tight cropping."""

import numpy as np

from assemble_sheet_music import clean_stray_marks, crop_to_content


def _white(h, w):
    return np.full((h, w, 3), 255, dtype=np.uint8)


def _main_block(img):
    """A wide, tall central content block: the 'real music'."""
    img[100:201, 60:341] = 0
    return img


def test_removes_isolated_top_left_mark_and_preserves_main():
    img = _main_block(_white(300, 400))
    img[5:15, 5:26] = 0  # thin (h=10), near top+left, isolated from main
    out, removed = clean_stray_marks(img)

    assert removed, "expected the stray mark to be reported as removed"
    assert (out[5:15, 5:26] == 255).all(), "stray mark should be whitened"
    assert (out[100:201, 60:341] == 0).all(), "main content must be untouched"


def test_removes_colored_corner_logo():
    img = _main_block(_white(300, 400))
    img[8:16, 360:395] = (200, 0, 0)  # red logo, top-right corner
    out, removed = clean_stray_marks(img)

    assert removed
    assert (out[8:16, 360:395] == 255).all()


def test_keeps_mark_far_from_all_edges():
    img = _main_block(_white(300, 400))
    img[210:216, 200:210] = 0  # not near any edge band
    out, removed = clean_stray_marks(img)

    assert (out[210:216, 200:210] == 0).all(), "interior mark must be kept"


def test_no_ink_returns_unchanged():
    img = _white(120, 120)
    out, removed = clean_stray_marks(img)
    assert removed == []
    assert (out == 255).all()


def test_crop_trims_to_content_bbox():
    img = _white(200, 200)
    img[50:80, 40:120] = 0
    cropped = crop_to_content(img)
    assert cropped.shape[0] == 30  # rows 50..79
    assert cropped.shape[1] == 80  # cols 40..119
    assert (cropped == 0).all()


def test_crop_all_white_is_unchanged():
    img = _white(50, 60)
    cropped = crop_to_content(img)
    assert cropped.shape == (50, 60, 3)
