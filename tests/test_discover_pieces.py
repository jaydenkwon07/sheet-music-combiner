"""Tests for input discovery, NFC normalization, and sequence validation."""

import unicodedata

import pytest

from assemble_sheet_music import DiscoveryError, discover_pieces


def _touch(dir_path, name):
    (dir_path / name).write_bytes(b"")


def test_finds_contiguous_sequence_sorted_by_n(tmp_path):
    for i in (3, 1, 2):  # created out of order on purpose
        _touch(tmp_path, f"Song_{i}.png")
    result = discover_pieces(tmp_path, "Song")
    assert [p.name for p in result] == ["Song_1.png", "Song_2.png", "Song_3.png"]


def test_ignores_unrelated_and_wrong_prefix(tmp_path):
    _touch(tmp_path, "Song_1.png")
    _touch(tmp_path, "Song_2.png")
    _touch(tmp_path, "Other_1.png")
    _touch(tmp_path, "Song_notes.txt")
    result = discover_pieces(tmp_path, "Song")
    assert [p.name for p in result] == ["Song_1.png", "Song_2.png"]


def test_missing_number_raises_and_names_gap(tmp_path):
    _touch(tmp_path, "Song_1.png")
    _touch(tmp_path, "Song_3.png")  # 2 missing
    with pytest.raises(DiscoveryError) as exc:
        discover_pieces(tmp_path, "Song")
    assert "2" in str(exc.value)


def test_duplicate_number_raises(tmp_path):
    _touch(tmp_path, "Song_1.png")
    _touch(tmp_path, "Song_01.png")  # both parse to n=1
    with pytest.raises(DiscoveryError) as exc:
        discover_pieces(tmp_path, "Song")
    assert "1" in str(exc.value)


def test_no_matches_raises(tmp_path):
    _touch(tmp_path, "Other_1.png")
    with pytest.raises(DiscoveryError):
        discover_pieces(tmp_path, "Song")


def test_must_start_at_1(tmp_path):
    _touch(tmp_path, "Song_2.png")
    _touch(tmp_path, "Song_3.png")
    with pytest.raises(DiscoveryError) as exc:
        discover_pieces(tmp_path, "Song")
    assert "1" in str(exc.value)


def test_nfc_normalization_of_korean_prefix(tmp_path):
    prefix = "약할때"
    # write a filename in NFD (decomposed) form; caller queries with NFC
    nfd_name = unicodedata.normalize("NFD", f"{prefix}_1.png")
    _touch(tmp_path, nfd_name)
    result = discover_pieces(tmp_path, unicodedata.normalize("NFC", prefix))
    assert len(result) == 1


def test_accepts_jpg_and_jpeg(tmp_path):
    _touch(tmp_path, "Song_1.jpg")
    _touch(tmp_path, "Song_2.jpeg")
    result = discover_pieces(tmp_path, "Song")
    assert [p.name for p in result] == ["Song_1.jpg", "Song_2.jpeg"]


def test_accepts_pdf(tmp_path):
    _touch(tmp_path, "Song_1.pdf")
    result = discover_pieces(tmp_path, "Song")
    assert [p.name for p in result] == ["Song_1.pdf"]


def test_accepts_mixed_extensions_under_one_prefix(tmp_path):
    _touch(tmp_path, "Song_1.png")
    _touch(tmp_path, "Song_2.jpg")
    _touch(tmp_path, "Song_3.pdf")
    result = discover_pieces(tmp_path, "Song")
    assert [p.name for p in result] == ["Song_1.png", "Song_2.jpg", "Song_3.pdf"]


def test_duplicate_across_extensions_raises(tmp_path):
    _touch(tmp_path, "Song_1.png")
    _touch(tmp_path, "Song_1.pdf")  # same n via different extension
    with pytest.raises(DiscoveryError) as exc:
        discover_pieces(tmp_path, "Song")
    assert "1" in str(exc.value)
