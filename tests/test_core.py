from pathlib import Path

import pymupdf
import pytest

from pdfmask.core import Area, Options, Preset, mask_document


def make_pdf(path: Path, rotate: int = 0, pages: int = 2) -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=842, height=595)
        page.insert_text((700, 560), "SECRET-LOGO", fontsize=14)
        page.insert_text((100, 100), "KEEP-ME", fontsize=14)
        page.draw_rect(pymupdf.Rect(690, 545, 800, 570))
        page.set_rotation(rotate)
    doc.save(path)
    return path


BOTTOM_RIGHT = Area(0.78, 0.88, 1.0, 1.0)


def test_text_under_area_is_removed(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    res = mask_document(src, tmp_path / "out" / "a.pdf", [BOTTOM_RIGHT], Options())
    assert res.pages_masked == 2
    with pymupdf.open(tmp_path / "out" / "a.pdf") as doc:
        for page in doc:
            text = page.get_text()
            assert "SECRET" not in text
            assert "KEEP-ME" in text


def test_line_art_inside_area_is_removed(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=1)
    mask_document(src, tmp_path / "b.pdf", [BOTTOM_RIGHT], Options())
    with pymupdf.open(tmp_path / "b.pdf") as doc:
        assert not any(d["rect"].intersects(pymupdf.Rect(690, 545, 800, 570))
                       for d in doc[0].get_drawings() if d.get("fill") is None)


def test_skips_other_orientation(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", rotate=90, pages=1)
    res = mask_document(src, tmp_path / "b.pdf", [BOTTOM_RIGHT],
                        Options(reference_landscape=True, skip_other_orientation=True))
    assert res.pages_skipped == 1 and res.pages_masked == 0


def test_refuses_to_overwrite_source(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    with pytest.raises(ValueError):
        mask_document(src, src, [BOTTOM_RIGHT], Options())


def test_preset_roundtrip(tmp_path):
    p = Preset([BOTTOM_RIGHT, Area(0.1, 0.1, 0.2, 0.2)], fill="black", skip_other_orientation=False)
    p.save(tmp_path / "p.json")
    q = Preset.load(tmp_path / "p.json")
    assert q.areas == p.areas and q.fill == "black" and q.skip_other_orientation is False


def test_frame_crossing_area_is_kept_unless_strict(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    frame = pymupdf.Rect(20, 20, 822, 575)
    page.draw_rect(frame, width=2)
    doc.save(tmp_path / "f.pdf")

    def frame_left(path):
        with pymupdf.open(path) as d:
            return any(abs(x["rect"].x0 - 20) < 2 and x.get("fill") is None for x in d[0].get_drawings())

    mask_document(tmp_path / "f.pdf", tmp_path / "keep.pdf", [BOTTOM_RIGHT], Options())
    mask_document(tmp_path / "f.pdf", tmp_path / "strict.pdf", [BOTTOM_RIGHT], Options(strict_lines=True))
    assert frame_left(tmp_path / "keep.pdf")
    assert not frame_left(tmp_path / "strict.pdf")
