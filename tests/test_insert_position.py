"""Tests for mapping an --at-position page:index to a flat insertion index."""

import pytest

from assemble_sheet_music import flat_index_for_position


def test_top_of_first_page():
    assert flat_index_for_position([6, 5], page=1, index=0) == 0


def test_within_first_page():
    assert flat_index_for_position([6, 5], page=1, index=3) == 3


def test_start_of_second_page():
    assert flat_index_for_position([6, 5], page=2, index=0) == 6


def test_within_second_page():
    assert flat_index_for_position([5, 5, 4], page=3, index=2) == 12


def test_rejects_page_out_of_range():
    with pytest.raises(ValueError):
        flat_index_for_position([6, 5], page=3, index=0)


def test_rejects_index_beyond_page():
    with pytest.raises(ValueError):
        flat_index_for_position([6, 5], page=1, index=6)  # page has 6 (0..5), 6 invalid
