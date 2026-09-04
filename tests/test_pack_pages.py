"""Tests for the height-aware page packer (pack_pages / sparse_page_warnings).

Unlike balance_pages (a fixed count per page), pack_pages sizes pages by
measured pixel height, so unusually tall snippets (e.g. voice + piano) land
fewer per page. All heights below are synthetic ints standing in for
piece_arrays[i].shape[0] -- these tests never build real images.
"""

from assemble_sheet_music import (
    REF_SNIPPET_HEIGHT_SPACINGS,
    STACK_GAP_PX,
    balance_pages,
    pack_pages,
    sparse_page_warnings,
)


def test_n11_uniform_splits_6_5():
    assert pack_pages([120] * 11, reference_spacing=10.0) == [6, 5]


def test_n15_uniform_splits_5_5_5():
    assert pack_pages([120] * 15, reference_spacing=10.0) == [5, 5, 5]


def test_n14_uniform_splits_5_5_4():
    assert pack_pages([120] * 14, reference_spacing=10.0) == [5, 5, 4]


def test_n7_uniform_resolves_4_3_automatically():
    # The old count-based gap (balance_pages(7) raises) is gone on this path.
    assert pack_pages([120] * 7, reference_spacing=10.0) == [4, 3]


def test_tall_snippets_pack_fewer_per_page():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s  # 120.0
    heights = [round(2.5 * h_ref)] * 12  # 300 each, ~voice+piano height
    assert pack_pages(heights, s) == [3, 3, 2, 2, 2]
    # Far fewer per page than the old fixed-count formula would give.
    assert max(pack_pages(heights, s)) < max(balance_pages(12))


def test_mixed_tall_and_short_partitions_optimally_in_order():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s
    tall = round(2.5 * h_ref)  # 300
    short = round(h_ref)  # 120
    heights = [tall, tall, tall, short, short, short, short, short, short]
    counts = pack_pages(heights, s)
    assert counts == [2, 3, 4]
    # Order is never reordered -- reconstruct the groups and check the first
    # page is tall-only and the last page is short-only, matching input order.
    pos = 0
    groups = []
    for c in counts:
        groups.append(heights[pos : pos + c])
        pos += c
    assert groups[0] == [tall, tall]
    assert groups[-1] == [short, short, short, short]


def test_every_piece_taller_than_budget_gets_its_own_page():
    s = 10.0
    budget = 6 * REF_SNIPPET_HEIGHT_SPACINGS * s + 5 * STACK_GAP_PX  # 920.0
    huge = round(budget * 2)
    assert pack_pages([huge] * 5, s) == [1, 1, 1, 1, 1]


def test_no_reference_delegates_to_balance_pages():
    heights = [999] * 11  # heights irrelevant when reference_spacing is None
    assert pack_pages(heights, None) == balance_pages(11)


def test_pages_sum_to_total_for_arbitrary_heights():
    heights = [50, 400, 120, 900, 60, 60, 60, 200]
    assert sum(pack_pages(heights, 10.0)) == len(heights)


def test_sparse_warning_emitted_for_disproportionate_split():
    s = 10.0
    h_ref = REF_SNIPPET_HEIGHT_SPACINGS * s  # 120.0
    heights = [round(5 * h_ref)] + [round(0.5 * h_ref)] * 4  # 600, 60x4
    counts = [1, 4]  # a huge snippet alone; four modest ones on the other page
    warnings = sparse_page_warnings(heights, counts, s)
    assert any("sparse" in w and "page 2" in w for w in warnings)
    assert not any("page 1" in w for w in warnings)


def test_sparse_warning_not_emitted_for_single_page():
    assert sparse_page_warnings([1], [1], 10.0) == []


def test_backward_compat_at_h_ref_matches_balance_pages_n6():
    assert pack_pages([360] * 6, reference_spacing=30.0) == balance_pages(6)


def test_backward_compat_at_h_ref_matches_balance_pages_n12():
    assert pack_pages([360] * 12, reference_spacing=30.0) == balance_pages(12)


def test_backward_compat_at_h_ref_matches_balance_pages_n13():
    assert pack_pages([360] * 13, reference_spacing=30.0) == balance_pages(13)


def test_backward_compat_at_h_ref_matches_balance_pages_n16():
    assert pack_pages([360] * 16, reference_spacing=30.0) == balance_pages(16)


def test_backward_compat_at_h_ref_matches_balance_pages_n18():
    assert pack_pages([360] * 18, reference_spacing=30.0) == balance_pages(18)


def test_empty_input_returns_no_pages():
    assert pack_pages([], reference_spacing=10.0) == []
