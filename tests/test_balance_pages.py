"""Tests for the page-count balancing formula."""

import pytest

from assemble_sheet_music import PageBalanceError, balance_pages


def test_n11_splits_6_5():
    assert balance_pages(11) == [6, 5]


def test_n15_splits_5_5_5():
    assert balance_pages(15) == [5, 5, 5]


def test_n14_splits_5_5_4():
    assert balance_pages(14) == [5, 5, 4]


def test_larger_pages_come_first():
    # remainder pages (base+1) should lead, matching the verified examples
    assert balance_pages(11)[0] >= balance_pages(11)[-1]
    assert balance_pages(14)[0] >= balance_pages(14)[-1]


def test_single_full_page():
    assert balance_pages(6) == [6]


def test_small_single_page_at_min():
    assert balance_pages(4) == [4]


def test_n7_is_the_gap_case_and_raises():
    with pytest.raises(PageBalanceError) as exc:
        balance_pages(7)
    # message should name both rejected options so the caller can decide
    msg = str(exc.value)
    assert "7" in msg


def test_every_n_except_7_satisfies_min_max_and_balance():
    # exhaustive sanity sweep: for N up to 60, the only value that cannot be
    # balanced within [4, 6] per page with a max spread of 1 is N=7.
    for n in range(4, 61):
        if n == 7:
            with pytest.raises(PageBalanceError):
                balance_pages(n)
            continue
        pages = balance_pages(n)
        assert sum(pages) == n
        assert all(4 <= p <= 6 for p in pages), (n, pages)
        assert max(pages) - min(pages) <= 1, (n, pages)
