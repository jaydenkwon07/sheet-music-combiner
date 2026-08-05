"""Tests for parsing the explicit --pages override."""

import pytest

from assemble_sheet_music import parse_pages_override


def test_parses_comma_list():
    assert parse_pages_override("5,5,4", total=14) == [5, 5, 4]


def test_tolerates_whitespace():
    assert parse_pages_override(" 6 , 5 ", total=11) == [6, 5]


def test_rejects_sum_mismatch():
    with pytest.raises(ValueError) as exc:
        parse_pages_override("5,5,5", total=14)
    assert "14" in str(exc.value)  # message names the expected total


def test_allows_out_of_band_counts_for_n7_correction():
    # override is authoritative: 4,3 breaks the min rule but is explicitly chosen
    assert parse_pages_override("4,3", total=7) == [4, 3]


def test_rejects_nonpositive_entry():
    with pytest.raises(ValueError):
        parse_pages_override("6,0", total=6)


def test_rejects_nonnumeric():
    with pytest.raises(ValueError):
        parse_pages_override("5,x", total=5)
