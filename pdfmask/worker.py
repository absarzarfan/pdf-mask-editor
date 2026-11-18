"""Runs the batch off the UI thread."""
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .core import Area, Options, mask_document


class MaskWorker(QThread):
    progress = Signal(int, int)          # done, total
    fileDone = Signal(str, bool, str)    # name, ok, message
    allDone = Signal(int, int, bool)     # ok, failed, cancelled

    def __init__(self, files: list[Path], out_dir: Path, areas: list[Area], opts: Options):
        super().__init__()
        self.files, self.out_dir, self.areas, self.opts = files, out_dir, areas, opts
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok = failed = 0
        total = len(self.files)
        for i, src in enumerate(self.files, 1):
            if self._cancel:
                break
            try:
                r = mask_document(src, self.out_dir / src.name, self.areas, self.opts)
                msg = f"{r.pages_masked} pages masked"
                if r.pages_skipped:
                    msg += f", {r.pages_skipped} skipped (other orientation)"
                ok += 1
                self.fileDone.emit(src.name, True, msg)
            except Exception as e:  # noqa: BLE001 — report every failure to the user
                failed += 1
                self.fileDone.emit(src.name, False, str(e))
            self.progress.emit(i, total)
        self.allDone.emit(ok, failed, self._cancel)
