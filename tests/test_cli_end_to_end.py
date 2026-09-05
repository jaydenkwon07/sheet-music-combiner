"""End-to-end pipeline tests against synthetic snippet images."""

import numpy as np
from PIL import Image

from assemble_sheet_music import (
    LETTER_HEIGHT_PX,
    LETTER_WIDTH_PX,
    assemble,
    main,
)


def _snippet(spacing=30, width=800, n_lines=5, stray=False, pad=197):
    """A synthetic 'system': 5 full-width staff lines at a fixed spacing,
    optionally with an isolated stray mark near the top-left corner.

    Default pad=197 gives a total height of EXACTLY 12x the staff spacing
    (40 + 30*4 + 3 + 197 = 360 at spacing=30), matching
    REF_SNIPPET_HEIGHT_SPACINGS precisely -- load-bearing now that
    pack_pages sizes pages by height: any overage here (the previous
    pad=200 gave height 363, a 0.8% overage) silently costs an extra page
    at exact-multiple-of-6 piece counts. Override pad to build an
    unusually tall ("voice + piano") snippet.
    """
    top, thick = 40, 3
    height = top + spacing * (n_lines - 1) + thick + pad
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + thick, 30 : width - 30] = 0  # inset so edges are whitespace
    if stray:
        img[5:13, 3:20] = 0  # thin, top-left, isolated
    return img


def _write_pieces(dir_path, prefix, n, **kw):
    for i in range(1, n + 1):
        Image.fromarray(_snippet(**kw)).save(dir_path / f"{prefix}_{i}.png")


def test_full_run_11_pieces(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 11, stray=True)

    summary = assemble(src, "Song", out)

    assert summary["counts"] == [6, 5]
    assert summary["pdf"].exists()
    assert len(summary["page_pngs"]) == 2
    for png in summary["page_pngs"]:
        with Image.open(png) as im:
            assert im.size == (LETTER_WIDTH_PX, LETTER_HEIGHT_PX)
    # stray marks were reported removed on at least one page
    assert any("stray" in w for w in summary["warnings"])


def test_pdf_has_expected_page_count(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 15)
    summary = assemble(src, "Song", out)
    assert summary["counts"] == [5, 5, 5]
    # PDF exists, is non-trivial, and embeds 3 page objects
    data = summary["pdf"].read_bytes()
    assert len(data) > 0
    assert data.count(b"/Type /Page\n") == 3


def test_n7_without_pages_resolves_automatically(tmp_path):
    # The height-aware packer removes the old N=7 gap: with a measurable
    # reference spacing, pack_pages resolves N=7 to [4, 3] without an error.
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 7)
    summary = assemble(src, "Song", out)
    assert summary["counts"] == [4, 3]
    assert summary["pdf"].exists()


def test_n7_with_no_measurable_reference_still_raises(tmp_path):
    # No staff lines anywhere -> normalize_piece_scales returns reference
    # spacing None -> pack_pages delegates to balance_pages(7), which still
    # raises for the N=7 gap. The fallback path keeps today's behaviour;
    # only the height-aware (measurable-reference) path resolves N=7
    # automatically.
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    for i in range(1, 8):
        Image.fromarray(np.full((120, 200, 3), 255, dtype=np.uint8)).save(
            src / f"Song_{i}.png"
        )
    code = main([
        "--input-dir", str(src),
        "--prefix", "Song",
        "--output-dir", str(out),
    ])
    assert code == 2


def test_n7_with_pages_override_succeeds(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 7)
    summary = assemble(src, "Song", out, pages_spec="4,3")
    assert summary["counts"] == [4, 3]
    assert summary["pdf"].exists()


def test_missing_piece_exits_nonzero(tmp_path, capsys):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 3)
    (src / "Song_2.png").unlink()  # create a gap
    code = main([
        "--input-dir", str(src),
        "--prefix", "Song",
        "--output-dir", str(out),
    ])
    assert code == 1
    assert "2" in capsys.readouterr().err


def test_insert_at_top_rescales_and_adds_piece(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 5, spacing=30)  # reference spacing 30
    # inserted snippet at 2x spacing -> should be rescaled ~0.5
    ins = tmp_path / "extra.png"
    Image.fromarray(_snippet(spacing=60)).save(ins)

    summary = assemble(src, "Song", out, insert=ins, at_top=True)

    assert summary["num_pieces"] == 6
    assert summary["counts"] == [6]
    assert summary["insert_factor"] == np.float64(0.5) or abs(summary["insert_factor"] - 0.5) < 0.05


def test_identical_inputs_produce_byte_identical_output(tmp_path):
    """Same snippets in -> same PDF/PNG bytes out, run to run (no timestamps,
    no randomness)."""
    src = tmp_path / "in"
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    src.mkdir()
    _write_pieces(src, "Song", 11, stray=True)

    summary1 = assemble(src, "Song", out1)
    summary2 = assemble(src, "Song", out2)

    assert summary1["pdf"].read_bytes() == summary2["pdf"].read_bytes()
    for p1, p2 in zip(summary1["page_pngs"], summary2["page_pngs"]):
        assert p1.read_bytes() == p2.read_bytes()


def test_at_position_uses_height_aware_pre_layout(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # 4 "tall" snippets (~2.5x a normal single-staff height) -- height-aware
    # packing splits these into 2 pages of 2 each, unlike balance_pages(4),
    # which would give a single page of 4 (no measurable-height awareness).
    _write_pieces(src, "Song", 4, spacing=30, pad=737)
    ins = tmp_path / "extra.png"
    Image.fromarray(_snippet(spacing=30, pad=737)).save(ins)

    summary = assemble(src, "Song", out, insert=ins, at_position="2:0")

    assert summary["num_pieces"] == 5
    # flat_index_for_position([2, 2], page=2, index=0) == 2: the pre-insert
    # layout put 2 tall snippets per page, so "page 2 index 0" lands right
    # after the first 2 originals. Post-insert, 5 uniform tall pieces pack
    # to [3, 2] (front-loaded, same base/remainder rule as the count-based
    # formula).
    assert summary["counts"] == [3, 2]


def test_sparse_page_warning_fires_for_a_genuinely_lopsided_split(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # 1 giant snippet (5x h_ref) + 4 modest ones (0.5x h_ref), spacing=30.
    # pack_pages puts the giant alone on page 1 and the 4 modest ones on
    # page 2; page 2's height (4*180 + 3*40 = 840) is well under 40% of the
    # budget (2360), so a sparse warning must fire for it.
    Image.fromarray(_snippet(spacing=30, pad=1637)).save(src / "Song_1.png")  # height 1800
    for i in range(2, 6):
        Image.fromarray(_snippet(spacing=30, pad=17)).save(src / f"Song_{i}.png")  # height 180

    summary = assemble(src, "Song", out)

    assert summary["counts"] == [1, 4]
    assert any("sparse" in w and "page 2" in w for w in summary["warnings"])


def test_different_width_pieces_normalize_to_matching_width(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    # Two pieces at very different raw widths (e.g. different screenshot
    # resolutions or staff structures) should end up with matching staff
    # width after normalization -- not just matching note size.
    Image.fromarray(_snippet(width=800)).save(src / "Song_1.png")
    Image.fromarray(_snippet(width=1600)).save(src / "Song_2.png")

    summary = assemble(src, "Song", out)

    assert summary["pdf"].exists()
    assert any("to match staff width" in w for w in summary["warnings"])

    # Confirm the widths actually converge: re-run the same normalization
    # step the pipeline used, directly on the same source files.
    from assemble_sheet_music import (
        discover_pieces,
        load_rgb,
        measure_content_width,
        normalize_piece_scales,
        strip_edge_border_lines,
    )

    paths = discover_pieces(src, "Song")
    pieces = [strip_edge_border_lines(load_rgb(p))[0] for p in paths]
    normalized, _warnings, _reference = normalize_piece_scales(pieces)
    widths = [measure_content_width(p) for p in normalized]
    assert abs(widths[0] - widths[1]) <= 2
