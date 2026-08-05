"""Tests for staff-line spacing measurement and rescale-factor calculation."""

import numpy as np
import pytest

from assemble_sheet_music import measure_staff_spacing, rescale_factor


def _staff_image(spacing, n_lines=5, width=200, line_thickness=2, top=20):
    """White image with `n_lines` full-width black rows at a fixed `spacing`."""
    height = top + spacing * (n_lines - 1) + line_thickness + 20
    img = np.full((height, width), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + line_thickness, :] = 0
    return img


def test_measures_known_spacing():
    img = _staff_image(spacing=30)
    assert measure_staff_spacing(img) == pytest.approx(30, abs=1)


def test_measures_a_different_spacing():
    img = _staff_image(spacing=48)
    assert measure_staff_spacing(img) == pytest.approx(48, abs=1)


def test_ignores_short_horizontal_marks():
    # a short dark run (a barline stub) shouldn't count as a staff line
    img = _staff_image(spacing=30)
    img[5:7, 0:10] = 0  # thin, only 10px wide out of 200 -> below 0.5 fraction
    assert measure_staff_spacing(img) == pytest.approx(30, abs=1)


def test_raises_when_no_staff_lines_found():
    img = np.full((100, 200), 255, dtype=np.uint8)
    with pytest.raises(ValueError):
        measure_staff_spacing(img)


def test_rescale_factor_is_reference_over_inserted():
    assert rescale_factor(reference_spacing=60, inserted_spacing=30) == pytest.approx(2.0)


def test_rescale_factor_below_one_when_inserted_is_larger():
    assert rescale_factor(reference_spacing=30, inserted_spacing=60) == pytest.approx(0.5)
