"""Tests for the physical page-height packer (pack_pages / sparse_page_warnings).

pack_pages splits the ordered piece list into the fewest contiguous pages whose
every page's stacked height fits a physical US-Letter page budget (usable height
at the width-bound uniform scale, filled to PAGE_FILL_FRACTION). Page counts
depend on the snippet ASPECT RATIO -- a common content width W (all pieces share
it after normalize_piece_scales) plus each piece's pixel height -- so these tests
pass realistic wide-staff dimensions, not the unit fixtures' unrealistic aspect.
"""

import math

from assemble_sheet_music import (
    DEFAULT_MARGIN_PX,
    LETTER_HEIGHT_PX,
    LETTER_WIDTH_PX,
    PAGE_FILL_FRACTION,
    STACK_GAP_PX,
    _page_height_budget,
    balance_pages,
    pack_pages,
    sparse_page_warnings,
)

# A realistic full-width staff snippet is far wider than tall (~4:1); these are
# the dimensions the packer is designed for.
REALISTIC_W = 1450


def _budget(width, margin=DEFAULT_MARGIN_PX):
    usable_w = LETTER_WIDTH_PX - 2 * margin
    usable_h = LETTER_HEIGHT_PX - 2 * margin
    return PAGE_FILL_FRACTION * usable_h * width / usable_w


def _min_tallest(heights, k, gap=STACK_GAP_PX):
    """Independent min-max linear-partition height for exactly k groups, to
    check pack_pages truly picks the fewest pages that fit the budget."""
    n = len(heights)
    pre = [0] * (n + 1)
    for i, h in enumerate(heights):
        pre[i + 1] = pre[i] + h

    def gh(a, b):
        return (pre[b] - pre[a]) + (b - a - 1) * gap

    dp = [[math.inf] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(j, n + 1):
            dp[i][j] = min(max(dp[s][j - 1], gh(s, i)) for s in range(j - 1, i))
    return dp[n][k]


def _page_heights(heights, counts, gap=STACK_GAP_PX):
    out, pos = [], 0
    for c in counts:
        grp = heights[pos : pos + c]
        pos += c
        out.append(sum(grp) + (len(grp) - 1) * gap)
    return out


def test_budget_is_physical_page_geometry():
    # 0.9 * usable_h * W / usable_w
    assert _page_height_budget(REALISTIC_W) == _budget(REALISTIC_W)
    assert _page_height_budget(1000, margin=0) == PAGE_FILL_FRACTION * LETTER_HEIGHT_PX * 1000 / LETTER_WIDTH_PX


def test_real_file_like_tall_set_packs_to_four_pages():
    # The measured, width-normalized heights of the real 11-piece file at W=1461.
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    assert pack_pages(heights, 1461) == [2, 3, 3, 3]


def test_single_staff_sets_pack_by_physical_fit():
    for n, expected in [
        (6, [3, 3]),
        (7, [4, 3]),
        (11, [4, 4, 3]),
        (14, [4, 4, 3, 3]),
        (15, [4, 4, 4, 3]),
    ]:
        assert pack_pages([360] * n, REALISTIC_W) == expected, (n, expected)


def test_pack_uses_the_fewest_pages_that_fit_the_budget():
    heights = [360] * 11
    counts = pack_pages(heights, REALISTIC_W)
    k = len(counts)
    b = _budget(REALISTIC_W)
    # k pages fit the budget...
    assert _min_tallest(heights, k) <= b
    # ...and k-1 pages do not (k is minimal).
    assert _min_tallest(heights, k - 1) > b


def test_every_page_within_budget_when_achievable():
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    counts = pack_pages(heights, 1461)
    b = _budget(1461)
    assert all(h <= b for h in _page_heights(heights, counts))


def test_single_system_taller_than_page_gets_its_own_page():
    # A 2500px system exceeds the budget (~1696) even alone -> clamp to N.
    assert pack_pages([2500, 300, 300, 300], REALISTIC_W) == [1, 1, 1, 1]


def test_small_set_that_fits_lands_on_one_page():
    # Two short systems fit one page (2*360+40 = 760 <= budget ~1696).
    assert pack_pages([360, 360], REALISTIC_W) == [2]


def test_no_reference_width_delegates_to_balance_pages():
    heights = [999] * 11  # heights irrelevant when reference_width is None
    assert pack_pages(heights, None) == balance_pages(11)


def test_pages_sum_to_total_for_arbitrary_heights():
    heights = [500, 400, 360, 900, 300, 360, 360, 200]
    assert sum(pack_pages(heights, REALISTIC_W)) == len(heights)


def test_empty_input_returns_no_pages():
    assert pack_pages([], REALISTIC_W) == []


def test_larger_margin_raises_the_page_height_budget():
    # The page is taller than it is wide, so shrinking both usable dimensions
    # by a larger margin raises the ratio usable_h/usable_w -- and thus the
    # height budget usable_h*W/usable_w. (Verifies margin actually affects the
    # budget, which pack_pages/sparse_page_warnings thread through.)
    assert _page_height_budget(1000, margin=200) > _page_height_budget(1000, margin=22)


def test_sparse_warning_fires_for_a_genuinely_lopsided_split():
    # One tall-but-in-budget system (1500 < budget ~1696) forces [1, 2]; the
    # 2-short-piece page (163+40+163 = 366) is under 40% of the budget (~678).
    heights = [1500, 163, 163]
    counts = pack_pages(heights, REALISTIC_W)
    assert counts == [1, 2]
    warnings = sparse_page_warnings(heights, counts, REALISTIC_W)
    assert any("sparse" in w and "page 2" in w for w in warnings)


def test_sparse_warning_not_emitted_for_single_page():
    assert sparse_page_warnings([360, 360], [2], REALISTIC_W) == []


def test_determinism_same_inputs_same_partition():
    heights = [794, 527, 506, 465, 437, 457, 458, 474, 419, 400, 376]
    assert pack_pages(heights, 1461) == pack_pages(heights, 1461)
