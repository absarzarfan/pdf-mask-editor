"""Runs the masking off the UI thread."""
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .core import Cancelled, FileResult, Layout, Options, mask_document


class MaskWorker(QThread):
    progress = Signal(int, int)          # done, total (pages for one file, files for a folder)
    fileDone = Signal(str, bool, str)    # name, ok, message
    fileResult = Signal(object)          # FileResult of a file that was written
    allDone = Signal(int, int, bool)     # ok, failed, cancelled

    def __init__(self, jobs: list[tuple[Path, Path]], layouts: list[Layout], opts: Options):
        """`jobs` is a list of (source, output) pairs; one pair for single-file mode."""
        super().__init__()
        self.jobs, self.layouts, self.opts = jobs, layouts, opts
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok = failed = 0
        single = len(self.jobs) == 1
        total = len(self.jobs)
        for i, (src, dst) in enumerate(self.jobs, 1):
            if self._cancel:
                break
            try:
                r: FileResult = mask_document(
                    src, dst, self.layouts, self.opts,
                    on_page=self.progress.emit if single else None,
                    should_cancel=lambda: self._cancel)
                msg = f"{r.pages_masked} of {r.page_count} pages masked"
                if r.pages_skipped:
                    msg += f", {r.pages_skipped} skipped (turned the other way)"
                if r.unchanged_pages:
                    msg += f", {len(r.unchanged_pages)} left unchanged"
                ok += 1
                self.fileDone.emit(src.name, True, msg)
                self.fileResult.emit(r)
            except Cancelled:
                break
            except Exception as e:  # noqa: BLE001 — report every failure to the user
                failed += 1
                self.fileDone.emit(src.name, False, str(e))
            if not single:
                self.progress.emit(i, total)
        self.allDone.emit(ok, failed, self._cancel)
