import numpy as np
from PIL import Image

from web.backend import assembler_bridge as br


def _snippet(spacing=30, width=800, n_lines=5):
    top, thick, pad = 40, 3, 40
    height = top + spacing * (n_lines - 1) + thick + pad
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + thick, 30 : width - 30] = 0
    return img


def _write(dir_path, prefix, n):
    dir_path.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        Image.fromarray(_snippet()).save(dir_path / f"{prefix}_{i}.png")


def test_derive_prefix_common():
    assert br.derive_prefix(["Song_1.png", "Song_2.png"]) == "Song"


def test_derive_prefix_ambiguous_returns_none():
    assert br.derive_prefix(["A_1.png", "B_1.png"]) is None


def test_validate_ok(tmp_path):
    _write(tmp_path, "Song", 5)
    r = br.validate_upload(tmp_path, "Song")
    assert r.ok and r.num_pieces == 5 and len(r.files) == 5


def test_validate_missing_number(tmp_path):
    _write(tmp_path, "Song", 3)
    (tmp_path / "Song_2.png").unlink()
    r = br.validate_upload(tmp_path, "Song")
    assert not r.ok and "2" in r.error


def test_run_assemble_ok(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    _write(src, "Song", 11)
    out.mkdir()
    r = br.run_assemble(src, "Song", out, margin=22, pages_spec=None)
    assert r.ok and r.counts == [6, 5]
    assert r.pdf_file == "Song.pdf"
    assert (out / r.pdf_file).exists()
    assert all((out / p).exists() for p in r.page_files)


def test_run_assemble_n7_needs_split(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    _write(src, "Song", 7)
    out.mkdir()
    r = br.run_assemble(src, "Song", out, margin=22, pages_spec=None)
    assert not r.ok and r.needs_split and "7" in r.options


def test_run_assemble_split_override(tmp_path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    _write(src, "Song", 7)
    out.mkdir()
    r = br.run_assemble(src, "Song", out, margin=22, pages_spec="4,3")
    assert r.ok and r.counts == [4, 3]
