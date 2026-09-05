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
