"""Tests for streaming PDF assembly from on-disk page PNGs (#2).

Building the PDF from the already-written page PNGs (opened lazily, one at a
time) instead of holding every full-resolution letter-page array in RAM keeps
peak memory low. Output must stay a valid, epoch-pinned, deterministic PDF.
"""

import numpy as np
from PIL import Image

from assemble_sheet_music import save_pdf_from_paths, save_png


def _page(color):
    return np.full((300, 200, 3), color, dtype=np.uint8)


def test_builds_pdf_with_one_object_per_page(tmp_path):
    paths = []
    for i, c in enumerate((240, 200, 160), start=1):
        p = tmp_path / f"page{i}.png"
        save_png(_page(c), p)
        paths.append(p)
    pdf = tmp_path / "out.pdf"
    save_pdf_from_paths(paths, pdf)
    data = pdf.read_bytes()
    assert data.count(b"/Type /Page\n") == 3


def test_output_is_deterministic(tmp_path):
    # Same inputs AND same output name (as the real pipeline always uses
    # {prefix}.pdf) must give byte-identical PDFs run to run -- no "now"
    # timestamps. PIL embeds the output stem as /Title, so the basename is
    # held fixed and only the directory differs, mirroring real reruns.
    src = tmp_path / "p.png"
    save_png(_page(210), src)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    save_pdf_from_paths([src], tmp_path / "a" / "Song.pdf")
    save_pdf_from_paths([src], tmp_path / "b" / "Song.pdf")
    assert (tmp_path / "a" / "Song.pdf").read_bytes() == (
        tmp_path / "b" / "Song.pdf"
    ).read_bytes()
