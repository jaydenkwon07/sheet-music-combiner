"""Tests for whole-set staff-spacing normalization (normalize_piece_scales).

A snippet exported at a different DPI has wider/narrower pixel staff spacing
and would otherwise render at a different physical note size than the rest.
normalize_piece_scales rescales every piece to a common (median) spacing so the
assembled page is uniform.
"""

import numpy as np

from assemble_sheet_music import measure_staff_spacing, normalize_piece_scales


def _rgb_staff(spacing, n_lines=5, width=200, line_thickness=2, top=20):
    """White RGB image with `n_lines` full-width black rows at `spacing`."""
    height = top + spacing * (n_lines - 1) + line_thickness + 20
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + line_thickness, :, :] = 0
    return img


def _gray(rgb):
    return rgb[..., :3].mean(axis=2)


def test_mismatched_pieces_end_at_matching_spacing():
    pieces = [_rgb_staff(spacing=30), _rgb_staff(spacing=60)]
    out, warnings, _reference = normalize_piece_scales(pieces)
    # Median of {30, 60} is 45, so both are rescaled toward it and end up
    # measuring the same spacing as each other.
    s0 = measure_staff_spacing(_gray(out[0]))
    s1 = measure_staff_spacing(_gray(out[1]))
    assert abs(s0 - s1) <= 2
    # The 60-spacing piece was shrunk; the 30-spacing piece was grown.
    assert out[1].shape[0] < pieces[1].shape[0]
    assert out[0].shape[0] > pieces[0].shape[0]
    assert len(warnings) == 2


def test_unmeasurable_piece_is_left_alone_others_normalized():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)  # no staff lines
    pieces = [_rgb_staff(spacing=30), blank, _rgb_staff(spacing=60)]
    out, warnings, _reference = normalize_piece_scales(pieces)
    assert out[1] is pieces[1]  # blank untouched (identity)
    assert any("unmeasurable" in w for w in warnings)
    # The two measurable pieces still got normalized to each other.
    s0 = measure_staff_spacing(_gray(out[0]))
    s2 = measure_staff_spacing(_gray(out[2]))
    assert abs(s0 - s2) <= 2


def test_already_consistent_set_is_unchanged():
    pieces = [_rgb_staff(spacing=30) for _ in range(3)]
    out, warnings, _reference = normalize_piece_scales(pieces)
    assert warnings == []
    for original, result in zip(pieces, out):
        assert result is original  # within tolerance -> no resample


def test_fewer_than_two_measurable_returns_unchanged():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)
    pieces = [_rgb_staff(spacing=30), blank]
    out, _warnings, _reference = normalize_piece_scales(pieces)
    for original, result in zip(pieces, out):
        assert result is original


def test_reference_spacing_is_the_median_of_measured():
    pieces = [_rgb_staff(spacing=30), _rgb_staff(spacing=60)]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference == 45.0


def test_reference_spacing_is_none_when_fewer_than_two_measurable():
    blank = np.full((120, 200, 3), 255, dtype=np.uint8)
    pieces = [_rgb_staff(spacing=30), blank]
    _out, _warnings, reference = normalize_piece_scales(pieces)
    assert reference is None
