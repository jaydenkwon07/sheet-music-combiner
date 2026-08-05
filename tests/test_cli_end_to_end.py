"""End-to-end pipeline tests against synthetic snippet images."""

import numpy as np
from PIL import Image

from assemble_sheet_music import (
    LETTER_HEIGHT_PX,
    LETTER_WIDTH_PX,
    assemble,
    main,
)


def _snippet(spacing=30, width=800, n_lines=5, stray=False):
    """A synthetic 'system': 5 full-width staff lines at a fixed spacing,
    optionally with an isolated stray mark near the top-left corner."""
    top, thick, pad = 40, 3, 40
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


def test_n7_without_pages_exits_nonzero(tmp_path, capsys):
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir()
    _write_pieces(src, "Song", 7)
    code = main([
        "--input-dir", str(src),
        "--prefix", "Song",
        "--output-dir", str(out),
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "7" in err


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
