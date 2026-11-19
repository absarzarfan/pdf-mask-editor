import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from pdfmask.core import (Area, Layout, LayoutError, Options, PageRangeError, Preset,
                          default_output_path, format_page_ranges, layout_index_for_page,
                          mask_document, parse_page_ranges, split_layout, uncovered_pages,
                          unmasked_pages)


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
ALL = [Layout("All", "1-end", [BOTTOM_RIGHT])]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ basic masking
def test_text_under_area_is_removed(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    res = mask_document(src, tmp_path / "out" / "a.pdf", ALL, Options())
    assert res.pages_masked == 2
    with pymupdf.open(tmp_path / "out" / "a.pdf") as doc:
        for page in doc:
            text = page.get_text()
            assert "SECRET" not in text
            assert "KEEP-ME" in text


def test_line_art_inside_area_is_removed(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=1)
    mask_document(src, tmp_path / "b.pdf", ALL, Options())
    with pymupdf.open(tmp_path / "b.pdf") as doc:
        assert not any(d["rect"].intersects(pymupdf.Rect(690, 545, 800, 570))
                       for d in doc[0].get_drawings() if d.get("fill") is None)


def test_skips_other_orientation(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", rotate=90, pages=1)
    res = mask_document(src, tmp_path / "b.pdf", ALL,
                        Options(reference_landscape=True, skip_other_orientation=True))
    assert res.pages_skipped == 1 and res.pages_masked == 0


def test_orientation_skip_is_per_layout(tmp_path):
    # page 1 is landscape, page 2 is portrait (rotated); each layout has its own reference
    doc = pymupdf.open()
    for rot in (0, 90):
        page = doc.new_page(width=842, height=595)
        page.insert_text((700, 560), "SECRET-LOGO", fontsize=14)
        page.set_rotation(rot)
    doc.save(tmp_path / "m.pdf")
    layouts = [Layout("Landscape", "1", [BOTTOM_RIGHT], reference_landscape=True),
               Layout("Portrait", "2", [Area(0.0, 0.0, 1.0, 1.0)], reference_landscape=True)]
    res = mask_document(tmp_path / "m.pdf", tmp_path / "o.pdf", layouts, Options())
    assert res.layouts[0].pages_masked == [1]
    assert res.layouts[1].pages_skipped == [2]


def test_refuses_to_overwrite_source(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    before = sha(src)
    with pytest.raises(ValueError):
        mask_document(src, src, ALL, Options())
    assert sha(src) == before


def test_frame_crossing_area_is_kept_unless_strict(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    frame = pymupdf.Rect(20, 20, 822, 575)
    page.draw_rect(frame, width=2)
    doc.save(tmp_path / "f.pdf")

    def frame_left(path):
        with pymupdf.open(path) as d:
            return any(abs(x["rect"].x0 - 20) < 2 and x.get("fill") is None for x in d[0].get_drawings())

    mask_document(tmp_path / "f.pdf", tmp_path / "keep.pdf", ALL, Options())
    mask_document(tmp_path / "f.pdf", tmp_path / "strict.pdf", ALL, Options(strict_lines=True))
    assert frame_left(tmp_path / "keep.pdf")
    assert not frame_left(tmp_path / "strict.pdf")


# -------------------------------------------------------------- page ranges
@pytest.mark.parametrize("text, expected", [
    ("1-30", set(range(1, 31))),
    ("31-33", {31, 32, 33}),
    ("34", {34}),
    ("1-30, 35-40", set(range(1, 31)) | set(range(35, 41))),
    ("35-end", set(range(35, 41))),
    ("  3 ,  5 - 7 ", {3, 5, 6, 7}),
    ("END", {40}),
    ("1-3, 2-5", {1, 2, 3, 4, 5}),          # duplicates and overlap inside one string
    ("2, 2, 2", {2}),
    ("5–8", {5, 6, 7, 8}),           # en dash typed by a word processor
])
def test_parse_valid_ranges(text, expected):
    assert parse_page_ranges(text, 40) == expected


@pytest.mark.parametrize("text, fragment", [
    ("", "Enter the pages"),
    ("   ", "Enter the pages"),
    ("0", "start at 1"),
    ("0-5", "start at 1"),
    ("30-1", "runs backwards"),
    ("41", "Page 41 doesn't exist"),
    ("35-50", "Page 50 doesn't exist"),
    ("abc", "not a page number"),
    ("1-", "not a page number"),
    ("1,,3", "empty entry"),
    ("1-2-3", "not a page number"),
    ("-5", "not a page number"),
])
def test_parse_invalid_ranges(text, fragment):
    with pytest.raises(PageRangeError) as exc:
        parse_page_ranges(text, 40)
    assert fragment in str(exc.value)


def test_end_follows_the_file_length():
    assert parse_page_ranges("3-end", 5) == {3, 4, 5}
    assert parse_page_ranges("3-end", 10) == set(range(3, 11))
    with pytest.raises(PageRangeError, match="1 page"):
        parse_page_ranges("2", 1)


def test_format_page_ranges_roundtrip():
    pages = set(range(1, 31)) | set(range(35, 41)) | {33}
    text = format_page_ranges(pages)
    assert text == "1-30, 33, 35-40"
    assert parse_page_ranges(text, 40) == pages
    assert format_page_ranges([]) == ""


# ------------------------------------------------------------ layouts / pages
MARKER = "TITLE-BLOCK"
# where the title block sits for each group of pages, and an area that covers it
POSITIONS = {
    "a": ((700, 560), Area(0.78, 0.88, 1.0, 1.0)),    # pages 1-30 and 35-40
    "b": ((60, 40), Area(0.0, 0.0, 0.30, 0.10)),      # pages 31-33
    "c": ((400, 300), Area(0.40, 0.45, 0.70, 0.55)),  # page 34
}


def group_of(n: int) -> str:
    return "b" if 31 <= n <= 33 else "c" if n == 34 else "a"


def make_moving_pdf(path: Path, pages: int = 40) -> Path:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=842, height=595)
        page.insert_text(POSITIONS[group_of(n)][0], MARKER, fontsize=14)
        page.insert_text((100, 100), f"KEEP-ME-{n}", fontsize=14)
        page.insert_text((300, 200), f"NOTE-{n}", fontsize=12)
    doc.save(path)
    return path


def three_layouts() -> list[Layout]:
    return [Layout("Moved", "31-33", [POSITIONS["b"][1]]),
            Layout("Normal", "1-30, 35-40", [POSITIONS["a"][1]]),
            Layout("Odd page", "34", [POSITIONS["c"][1]])]


def test_moving_title_block_is_removed_on_every_page(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf")
    dst = tmp_path / "plan_masked.pdf"
    res = mask_document(src, dst, three_layouts(), Options())

    assert res.pages_masked == 40 and res.unchanged_pages == []
    assert [len(lr.pages_masked) for lr in res.layouts] == [3, 36, 1]
    with pymupdf.open(dst) as doc:
        assert len(doc) == 40
        for n, page in enumerate(doc, 1):
            text = page.get_text()
            assert MARKER not in text, f"marker left on page {n}"
            assert f"KEEP-ME-{n}" in text, f"unrelated text lost on page {n}"
            assert f"NOTE-{n}" in text


def test_a_single_global_layout_would_miss_moved_pages(tmp_path):
    # guards the test above: with one layout the marker really survives on 31-34
    src = make_moving_pdf(tmp_path / "plan.pdf")
    mask_document(src, tmp_path / "o.pdf", [Layout("One", "1-end", [POSITIONS["a"][1]])], Options())
    with pymupdf.open(tmp_path / "o.pdf") as doc:
        left = [n for n, p in enumerate(doc, 1) if MARKER in p.get_text()]
    assert left == [31, 32, 33, 34]


def test_uncovered_pages_are_copied_unchanged(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=6)
    layouts = [Layout("First two", "1-2", [POSITIONS["a"][1]])]
    res = mask_document(src, tmp_path / "o.pdf", layouts, Options())
    assert res.unchanged_pages == [3, 4, 5, 6]
    assert uncovered_pages(layouts, 6) == [3, 4, 5, 6]
    with pymupdf.open(tmp_path / "o.pdf") as doc:
        assert len(doc) == 6
        assert MARKER not in doc[0].get_text() and MARKER not in doc[1].get_text()
        for n in (3, 4, 5, 6):  # same text as the source, marker included
            assert MARKER in doc[n - 1].get_text()
            assert f"KEEP-ME-{n}" in doc[n - 1].get_text()


def test_layout_without_areas_leaves_its_pages_unchanged(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=3)
    layouts = [Layout("Empty", "1-3", [])]
    assert unmasked_pages(layouts, 3) == [1, 2, 3]
    assert uncovered_pages(layouts, 3) == []
    res = mask_document(src, tmp_path / "o.pdf", layouts, Options())
    assert res.unchanged_pages == [1, 2, 3] and res.pages_masked == 0


def test_overlapping_layouts_raise_and_write_nothing(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=10)
    layouts = [Layout("A", "1-6", [BOTTOM_RIGHT]), Layout("B", "5-end", [BOTTOM_RIGHT])]
    with pytest.raises(LayoutError, match="both cover pages 5-6"):
        mask_document(src, tmp_path / "o.pdf", layouts, Options())
    assert not (tmp_path / "o.pdf").exists()
    assert not list(tmp_path.glob("*.part"))


def test_range_beyond_page_count_is_a_layout_error(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=4)
    with pytest.raises(LayoutError, match="Page 9 doesn't exist"):
        mask_document(src, tmp_path / "o.pdf", [Layout("A", "1-9", [BOTTOM_RIGHT])], Options())


def test_cancel_writes_nothing(tmp_path):
    from pdfmask.core import Cancelled
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=4)
    with pytest.raises(Cancelled):
        mask_document(src, tmp_path / "o.pdf", ALL, Options(), should_cancel=lambda: True)
    assert not (tmp_path / "o.pdf").exists()
    assert not list(tmp_path.glob("*.part"))


def test_split_layout_starts_a_new_layout_with_copied_areas():
    layouts = [Layout("Layout 1", "1-end", [BOTTOM_RIGHT], reference_landscape=True)]
    out = split_layout(layouts, 0, 31, 40)
    assert [(lay.name, lay.ranges) for lay in out] == [("Layout 1", "1-30"), ("Layout 2", "31-40")]
    assert out[1].areas == out[0].areas == [BOTTOM_RIGHT]
    assert out[1].areas is not out[0].areas          # independent copies
    assert out[1].reference_landscape is True
    assert layouts[0].ranges == "1-end"                # input untouched
    with pytest.raises(ValueError, match="already starts"):
        split_layout(out, 0, 1, 40)


def test_split_layout_keeps_non_contiguous_pages_together():
    layouts = [Layout("Layout 1", "1-30, 35-40", [BOTTOM_RIGHT])]
    out = split_layout(layouts, 0, 36, 40)
    assert [lay.ranges for lay in out] == ["1-30, 35", "36-40"]


def test_layout_index_for_page():
    layouts = three_layouts()
    assert layout_index_for_page(layouts, 32, 40) == 0
    assert layout_index_for_page(layouts, 36, 40) == 1
    assert layout_index_for_page(layouts, 34, 40) == 2
    assert layout_index_for_page([Layout("X", "1-5")], 9, 40) is None


# ------------------------------------------------------------------ presets
def test_preset_v2_roundtrip(tmp_path):
    layouts = [Layout("Title block", "1-30, 35-end", [BOTTOM_RIGHT, Area(0.1, 0.1, 0.2, 0.2)], True),
               Layout("Moved", "31-34", [Area(0.0, 0.0, 0.3, 0.1)])]
    p = Preset(layouts, fill="black", skip_other_orientation=False)
    p.save(tmp_path / "p.json")
    assert json.loads((tmp_path / "p.json").read_text())["version"] == 2
    q = Preset.load(tmp_path / "p.json")
    assert q.layouts == layouts and q.fill == "black" and q.skip_other_orientation is False


def test_preset_v1_loads_as_one_layout_over_all_pages(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({
        "version": 1, "fill": "black", "skip_other_orientation": False,
        "areas": [{"x0": 0.9, "y0": 0.9, "x1": 0.5, "y1": 0.5}, {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}],
    }), encoding="utf-8")
    q = Preset.load(tmp_path / "old.json")
    assert len(q.layouts) == 1
    layout = q.layouts[0]
    assert layout.pages(7) == set(range(1, 8)) and layout.pages(40) == set(range(1, 41))
    assert layout.areas[0] == Area(0.5, 0.5, 0.9, 0.9)   # normalised on load
    assert len(layout.areas) == 2 and q.fill == "black" and q.skip_other_orientation is False


def test_preset_without_version_is_v1_and_future_versions_are_refused():
    assert Preset.from_dict({"areas": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]}).layouts[0].ranges == "1-end"
    with pytest.raises(ValueError, match="newer"):
        Preset.from_dict({"version": 3})


# --------------------------------------------------------- single file mode
def test_single_file_mode_writes_one_file_and_keeps_the_source(tmp_path):
    folder = tmp_path / "drawings"
    folder.mkdir()
    src = make_moving_pdf(folder / "plan.pdf")
    before = sha(src)
    dst = default_output_path(src)
    assert dst == folder / "plan_masked.pdf"

    res = mask_document(src, dst, three_layouts(), Options())

    assert sorted(p.name for p in folder.iterdir()) == ["plan.pdf", "plan_masked.pdf"]
    assert sha(src) == before
    with pymupdf.open(src) as a, pymupdf.open(dst) as b:
        assert len(a) == len(b) == res.page_count == 40


def test_replacing_an_existing_output_leaves_no_partial_file(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=3)
    dst = tmp_path / "plan_masked.pdf"
    dst.write_bytes(b"old")
    mask_document(src, dst, ALL, Options())
    with pymupdf.open(dst) as doc:
        assert len(doc) == 3
    assert not list(tmp_path.glob("*.part"))


def test_summary_lines_report_layouts_and_unchanged_pages(tmp_path):
    src = make_moving_pdf(tmp_path / "plan.pdf", pages=8)
    layouts = [Layout("Normal", "1-3, 6-7", [POSITIONS["a"][1]]), Layout("Odd", "4", [POSITIONS["c"][1]])]
    res = mask_document(src, tmp_path / "o.pdf", layouts, Options())
    assert res.summary_lines() == ["Normal: 5 pages masked (1-3, 6-7)",
                                   "Odd: 1 page masked (4)",
                                   "Left unchanged: 5, 8"]
