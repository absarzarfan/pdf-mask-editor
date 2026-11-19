"""Redaction engine. No Qt imports here so it can be tested and reused headless."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pymupdf


@dataclass(frozen=True)
class Area:
    """A rectangle stored as fractions (0..1) of the visible page."""
    x0: float
    y0: float
    x1: float
    y1: float

    def normalized(self) -> "Area":
        x0, x1 = sorted((self.x0, self.x1))
        y0, y1 = sorted((self.y0, self.y1))
        clamp = lambda v: min(1.0, max(0.0, v))
        return Area(clamp(x0), clamp(y0), clamp(x1), clamp(y1))

    def to_page_rect(self, page: pymupdf.Page) -> pymupdf.Rect:
        view = page.rect  # visible (rotation-aware) size
        r = pymupdf.Rect(
            view.x0 + self.x0 * view.width,
            view.y0 + self.y0 * view.height,
            view.x0 + self.x1 * view.width,
            view.y0 + self.y1 * view.height,
        )
        r = r * page.derotation_matrix  # back to unrotated page space
        r.normalize()
        return r


@dataclass
class Options:
    fill: tuple[float, float, float] = (1.0, 1.0, 1.0)
    skip_other_orientation: bool = True
    # Fallback for layouts that don't record their own reference orientation.
    reference_landscape: bool | None = None
    clear_metadata: bool = True
    # False: delete vector lines fully inside an area; lines that only cross it
    #        (drawing frames, table borders) are kept and painted over.
    # True:  delete every line that touches an area, even the drawing frame.
    strict_lines: bool = False


class PageRangeError(ValueError):
    """A page-range string could not be understood."""


class LayoutError(ValueError):
    """The layouts can't be applied to a document (bad range or overlap)."""


class Cancelled(Exception):
    """Raised inside mask_document when the caller asks to stop."""


# ------------------------------------------------------------- page ranges
def _page_number(token: str, page_count: int) -> int:
    return page_count if token.lower() == "end" else int(token)


def parse_page_ranges(text: str, page_count: int) -> set[int]:
    """Turn "1-30, 35-end" into a set of 1-based page numbers.

    Raises PageRangeError with a message that can be shown to the user as is.
    """
    if page_count < 1:
        raise PageRangeError("This file has no pages.")
    text = text.replace("–", "-").replace("—", "-")
    if not text.strip():
        raise PageRangeError("Enter the pages, for example 1-30 or 1-30, 35-end.")
    pages: set[int] = set()
    for part in (p.strip() for p in text.split(",")):
        if not part:
            raise PageRangeError("There is an empty entry between commas.")
        m = re.fullmatch(r"(\d+|end)\s*(?:-\s*(\d+|end))?", part, re.IGNORECASE)
        if not m:
            raise PageRangeError(f'"{part}" is not a page number or a range like 3-7.')
        lo = _page_number(m.group(1), page_count)
        hi = _page_number(m.group(2), page_count) if m.group(2) is not None else lo
        if lo == 0 or hi == 0:
            raise PageRangeError("Pages start at 1, not 0.")
        if lo > hi:
            raise PageRangeError(f'"{part}" runs backwards. Write it as {hi}-{lo}.')
        if hi > page_count:
            noun = "page" if page_count == 1 else "pages"
            raise PageRangeError(f"Page {hi} doesn't exist. This file has {page_count} {noun}.")
        pages.update(range(lo, hi + 1))
    return pages


def format_page_ranges(pages: Iterable[int]) -> str:
    """Inverse of parse_page_ranges: {1, 2, 3, 7} -> "1-3, 7"."""
    nums = sorted(set(pages))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


# ----------------------------------------------------------------- layouts
@dataclass
class Layout:
    """A set of pages that share the same mask areas.

    Pages are kept as the range text the user typed ("1-30, 35-end") so that
    "end" still means the last page when the same layout meets a file of
    another length. Use pages(page_count) for the actual set of numbers.
    """
    name: str
    ranges: str = ""
    areas: list[Area] = field(default_factory=list)
    # Orientation of the page this layout was drawn on; None = not recorded.
    reference_landscape: bool | None = None

    @classmethod
    def from_pages(cls, name: str, pages: Iterable[int], areas: Iterable[Area] = (),
                   reference_landscape: bool | None = None) -> "Layout":
        return cls(name, format_page_ranges(pages), list(areas), reference_landscape)

    def pages(self, page_count: int) -> set[int]:
        if not self.ranges.strip():
            return set()
        return parse_page_ranges(self.ranges, page_count)


def resolve_layouts(layouts: list[Layout], page_count: int) -> dict[int, int]:
    """Map each covered page to the index of its layout.

    Raises LayoutError for a bad page range or when two layouts share a page.
    """
    owner: dict[int, int] = {}
    clashes: dict[tuple[int, int], set[int]] = {}
    for i, layout in enumerate(layouts):
        try:
            pages = layout.pages(page_count)
        except PageRangeError as e:
            raise LayoutError(f"{layout.name}: {e}") from e
        for page in sorted(pages):
            if page in owner:
                clashes.setdefault((owner[page], i), set()).add(page)
            else:
                owner[page] = i
    if clashes:
        msgs = [f'"{layouts[a].name}" and "{layouts[b].name}" both cover '
                f"{'page' if len(pg) == 1 else 'pages'} {format_page_ranges(pg)}."
                for (a, b), pg in clashes.items()]
        raise LayoutError("Layouts can't share pages. " + " ".join(msgs))
    return owner


def uncovered_pages(layouts: list[Layout], page_count: int) -> list[int]:
    """Pages that belong to no layout."""
    owner = resolve_layouts(layouts, page_count)
    return [p for p in range(1, page_count + 1) if p not in owner]


def unmasked_pages(layouts: list[Layout], page_count: int) -> list[int]:
    """Pages that will be copied unchanged: no layout, or a layout without areas."""
    owner = resolve_layouts(layouts, page_count)
    return [p for p in range(1, page_count + 1)
            if p not in owner or not layouts[owner[p]].areas]


def layout_index_for_page(layouts: list[Layout], page: int, page_count: int) -> int | None:
    """Index of the first layout that contains `page`, ignoring layouts with bad ranges."""
    for i, layout in enumerate(layouts):
        try:
            if page in layout.pages(page_count):
                return i
        except PageRangeError:
            continue
    return None


def next_layout_name(layouts: list[Layout]) -> str:
    used = {layout.name for layout in layouts}
    n = len(layouts) + 1
    while f"Layout {n}" in used:
        n += 1
    return f"Layout {n}"


def split_layout(layouts: list[Layout], index: int, page: int, page_count: int) -> list[Layout]:
    """Start a new layout at `page`.

    Pages of layouts[index] from `page` onward move to a new layout placed
    right after it, with a copy of the same areas. Returns a new list; raises
    ValueError when there is nothing to split.
    """
    layout = layouts[index]
    pages = layout.pages(page_count)
    if page not in pages:
        raise ValueError("This page isn't part of that layout.")
    earlier = {p for p in pages if p < page}
    if not earlier:
        raise ValueError(f'This page already starts "{layout.name}".')
    later = pages - earlier
    first = Layout.from_pages(layout.name, earlier, layout.areas, layout.reference_landscape)
    second = Layout.from_pages(next_layout_name(layouts), later, layout.areas,
                               layout.reference_landscape)
    out = list(layouts)
    out[index:index + 1] = [first, second]
    return out


# ------------------------------------------------------------------ results
@dataclass
class LayoutResult:
    name: str
    pages_masked: list[int] = field(default_factory=list)
    pages_skipped: list[int] = field(default_factory=list)  # turned the other way


@dataclass
class FileResult:
    source: Path
    output: Path | None = None
    page_count: int = 0
    pages_masked: int = 0
    pages_skipped: int = 0
    layouts: list[LayoutResult] = field(default_factory=list)
    unchanged_pages: list[int] = field(default_factory=list)  # no layout / empty layout
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary_lines(self) -> list[str]:
        lines = []
        for lr in self.layouts:
            if lr.pages_masked:
                n = len(lr.pages_masked)
                lines.append(f"{lr.name}: {n} {'page' if n == 1 else 'pages'} masked "
                             f"({format_page_ranges(lr.pages_masked)})")
            if lr.pages_skipped:
                lines.append(f"{lr.name}: pages {format_page_ranges(lr.pages_skipped)} "
                             "skipped (turned the other way)")
        if self.unchanged_pages:
            lines.append(f"Left unchanged: {format_page_ranges(self.unchanged_pages)}")
        return lines


def is_landscape(page: pymupdf.Page) -> bool:
    return page.rect.width >= page.rect.height


def default_output_path(src: Path) -> Path:
    """<name>_masked.pdf next to the source."""
    return src.with_name(f"{src.stem}_masked.pdf")


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def mask_document(src: Path, dst: Path, layouts: list[Layout], opts: Options,
                  on_page: Callable[[int, int], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> FileResult:
    """Write a copy of `src` to `dst` with each page masked by the layout that contains it.

    Pages that belong to no layout are copied unchanged, so the output always
    has the same pages as the source. Nothing is written if validation fails
    or the caller cancels.
    """
    result = FileResult(source=src, output=dst)
    if same_file(src, dst):
        raise ValueError("Output file must be different from the source file.")

    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError("File is password-protected.")
        total = len(doc)
        result.page_count = total
        owner = resolve_layouts(layouts, total)
        result.layouts = [LayoutResult(layout.name) for layout in layouts]
        areas_by_layout = [[a.normalized() for a in layout.areas] for layout in layouts]

        for number, page in enumerate(doc, 1):
            if should_cancel and should_cancel():
                raise Cancelled()
            idx = owner.get(number)
            if idx is None or not areas_by_layout[idx]:
                result.unchanged_pages.append(number)
            else:
                ref = layouts[idx].reference_landscape
                if ref is None:
                    ref = opts.reference_landscape
                if opts.skip_other_orientation and ref is not None and is_landscape(page) != ref:
                    result.layouts[idx].pages_skipped.append(number)
                    result.pages_skipped += 1
                else:
                    for area in areas_by_layout[idx]:
                        page.add_redact_annot(area.to_page_rect(page), fill=opts.fill)
                    page.apply_redactions(
                        images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                        graphics=(pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if opts.strict_lines
                                  else pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED),
                        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
                    )
                    result.layouts[idx].pages_masked.append(number)
                    result.pages_masked += 1
            if on_page:
                on_page(number, total)

        if opts.clear_metadata:
            doc.set_metadata({})
            doc.del_xml_metadata()
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + ".part")
        try:
            doc.save(part, garbage=4, deflate=True, clean=True)
            os.replace(part, dst)
        finally:
            part.unlink(missing_ok=True)
    return result


def list_pdfs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf" and p.is_file())


# ------------------------------------------------------------------ presets
PRESET_VERSION = 2


@dataclass
class Preset:
    layouts: list[Layout] = field(default_factory=list)
    fill: str = "white"
    skip_other_orientation: bool = True

    def to_dict(self) -> dict:
        return {"version": PRESET_VERSION, "fill": self.fill,
                "skip_other_orientation": self.skip_other_orientation,
                "layouts": [{"name": lay.name, "pages": lay.ranges,
                             "reference_landscape": lay.reference_landscape,
                             "areas": [asdict(a) for a in lay.areas]} for lay in self.layouts]}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        version = data.get("version", 1)
        if version == 1:
            # one global list of areas: a single layout covering every page
            areas = [Area(**a).normalized() for a in data.get("areas", [])]
            layouts = [Layout("Layout 1", "1-end", areas)] if areas else []
        elif version == 2:
            layouts = [Layout(name=str(item.get("name", f"Layout {i}")),
                              ranges=str(item.get("pages", "")),
                              areas=[Area(**a).normalized() for a in item.get("areas", [])],
                              reference_landscape=item.get("reference_landscape"))
                       for i, item in enumerate(data.get("layouts", []), 1)]
        else:
            raise ValueError(f"Preset version {version} is newer than this app understands.")
        return cls(layouts=layouts, fill=data.get("fill", "white"),
                   skip_other_orientation=bool(data.get("skip_other_orientation", True)))

    @classmethod
    def load(cls, path: Path) -> "Preset":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
