"""Redaction engine. No Qt imports here so it can be tested and reused headless."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

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
    reference_landscape: bool | None = None
    clear_metadata: bool = True
    # False: delete vector lines fully inside an area; lines that only cross it
    #        (drawing frames, table borders) are kept and painted over.
    # True:  delete every line that touches an area, even the drawing frame.
    strict_lines: bool = False


@dataclass
class FileResult:
    source: Path
    pages_masked: int = 0
    pages_skipped: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def is_landscape(page: pymupdf.Page) -> bool:
    return page.rect.width >= page.rect.height


def mask_document(src: Path, dst: Path, areas: Iterable[Area], opts: Options) -> FileResult:
    """Permanently remove everything under `areas` on every page of `src`."""
    areas = [a.normalized() for a in areas]
    result = FileResult(source=src)
    if src.resolve() == dst.resolve():
        raise ValueError("Output file must be different from the source file.")

    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError("File is password-protected.")
        for page in doc:
            if (opts.skip_other_orientation and opts.reference_landscape is not None
                    and is_landscape(page) != opts.reference_landscape):
                result.pages_skipped += 1
                continue
            for area in areas:
                page.add_redact_annot(area.to_page_rect(page), fill=opts.fill)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=(pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if opts.strict_lines
                          else pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED),
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            result.pages_masked += 1
        if opts.clear_metadata:
            doc.set_metadata({})
            doc.del_xml_metadata()
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dst, garbage=4, deflate=True, clean=True)
    return result


def list_pdfs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf" and p.is_file())


# ------------------------------------------------------------------ presets
@dataclass
class Preset:
    areas: list[Area] = field(default_factory=list)
    fill: str = "white"
    skip_other_orientation: bool = True

    def save(self, path: Path) -> None:
        data = {"version": 1, "fill": self.fill,
                "skip_other_orientation": self.skip_other_orientation,
                "areas": [asdict(a) for a in self.areas]}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Preset":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            areas=[Area(**a).normalized() for a in data.get("areas", [])],
            fill=data.get("fill", "white"),
            skip_other_orientation=bool(data.get("skip_other_orientation", True)),
        )
