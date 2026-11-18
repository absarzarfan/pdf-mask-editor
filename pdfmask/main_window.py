"""Main application window."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QImage, QKeySequence, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import APP_AUTHOR, APP_CREDIT, APP_NAME, APP_VERSION
from .canvas import PreviewView
from .core import Area, Options, Preset, is_landscape, list_pdfs
from .resources import app_icon
from .worker import MaskWorker

FILLS = {"White": (1.0, 1.0, 1.0), "Black": (0.0, 0.0, 0.0)}
PREVIEW_PX = 2200


def _divider() -> QFrame:
    d = QFrame(); d.setObjectName("Divider")
    return d


def _check_pixmap(dpr: float) -> QPixmap:
    size = 20
    pm = QPixmap(int(size * dpr), int(size * dpr)); pm.setDevicePixelRatio(dpr); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("white"), 2); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([QPointF(6, 10.5), QPointF(9, 13.5), QPointF(14.5, 7)])
    p.end()
    return pm


class Step(QWidget):
    """Numbered sidebar section. The steps really are a sequence, so they are numbered."""

    def __init__(self, number: int, title: str, hint: str = ""):
        super().__init__()
        self.number = number
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        head = QHBoxLayout(); head.setSpacing(10)
        self.badge = QLabel(str(number)); self.badge.setObjectName("StepNumber")
        t = QLabel(title); t.setObjectName("StepTitle")
        head.addWidget(self.badge); head.addWidget(t); head.addStretch()
        lay.addLayout(head)
        if hint:
            h = QLabel(hint); h.setObjectName("Hint"); h.setWordWrap(True)
            lay.addWidget(h)
        self.body = QVBoxLayout(); self.body.setSpacing(6)
        lay.addLayout(self.body)

    def set_done(self, done: bool):
        if done:
            self.badge.setText("")
            self.badge.setPixmap(_check_pixmap(self.badge.devicePixelRatioF()))
        else:
            self.badge.setPixmap(QPixmap())
            self.badge.setText(str(self.number))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)

        self.settings = QSettings("absarz software", "PDF Mask Editor")
        self.files: list[Path] = []
        self.current: Path | None = None
        self.page_index = 0
        self.page_count = 0
        self.areas: list[Area] = []
        self.ref_landscape: bool | None = None
        self.worker: MaskWorker | None = None
        self._render_zoom = 1.0

        self._build_ui()
        self._build_actions()
        self._restore()
        self._refresh()

    # ================================================================ layout
    def _build_ui(self):
        root = QWidget(); root.setObjectName("Root")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.setCentralWidget(root)

        # ---- sidebar
        side = QWidget(); side.setObjectName("Sidebar"); side.setFixedWidth(360)
        side.setAttribute(Qt.WA_StyledBackground)
        side_scroll = QScrollArea(); side_scroll.setWidget(side); side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.NoFrame); side_scroll.setFixedWidth(360)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s = QVBoxLayout(side); s.setContentsMargins(22, 20, 22, 18); s.setSpacing(16)

        brand = QVBoxLayout(); brand.setSpacing(0)
        name = QLabel(APP_NAME); name.setStyleSheet("font-size: 15pt; font-weight: 700;")
        by = QLabel(f"by {APP_AUTHOR}"); by.setObjectName("Hint")
        brand.addWidget(name); brand.addWidget(by)
        s.addLayout(brand)
        s.addWidget(_divider())

        # step 1
        self.step_src = Step(1, "Source folder", "Your original PDFs stay untouched.")
        row = QHBoxLayout()
        self.src_edit = QLineEdit(); self.src_edit.setReadOnly(True); self.src_edit.setPlaceholderText("No folder chosen")
        b = QPushButton("Browse"); b.clicked.connect(self.choose_source)
        row.addWidget(self.src_edit, 1); row.addWidget(b)
        self.step_src.body.addLayout(row)
        s.addWidget(self.step_src)

        # step 2
        self.step_out = Step(2, "Save masked copies to")
        row = QHBoxLayout()
        self.out_edit = QLineEdit(); self.out_edit.setReadOnly(True); self.out_edit.setPlaceholderText("No folder chosen")
        b = QPushButton("Browse"); b.clicked.connect(self.choose_output)
        row.addWidget(self.out_edit, 1); row.addWidget(b)
        self.step_out.body.addLayout(row)
        s.addWidget(self.step_out)

        # step 3
        self.step_areas = Step(3, "Mark the areas to hide",
                               "Drag on the drawing to add an area. Drag an area to move it, "
                               "pull its corner to resize, press Delete to remove it.")
        self.area_list = QListWidget(); self.area_list.setFixedHeight(118)
        self.area_list.currentRowChanged.connect(lambda r: self.view.select_index(r))
        self.step_areas.body.addWidget(self.area_list)
        row = QHBoxLayout(); row.setSpacing(4)
        for text, slot in (("Remove", self.remove_area), ("Clear all", self.clear_areas),
                           ("Load preset", self.load_preset), ("Save preset", self.save_preset)):
            btn = QPushButton(text); btn.setObjectName("Ghost"); btn.clicked.connect(slot)
            row.addWidget(btn)
            setattr(self, "btn_" + text.lower().replace(" ", "_"), btn)
        row.addStretch()
        self.step_areas.body.addLayout(row)
        s.addWidget(self.step_areas)

        # step 4
        self.step_run = Step(4, "Mask every drawing")
        row = QHBoxLayout()
        lbl = QLabel("Cover with"); lbl.setObjectName("Hint")
        self.fill_combo = QComboBox(); self.fill_combo.addItems(list(FILLS))
        row.addWidget(lbl); row.addWidget(self.fill_combo); row.addStretch()
        self.step_run.body.addLayout(row)
        self.skip_check = QCheckBox("Skip pages turned the other way\nfrom the preview page")
        self.skip_check.setChecked(True)
        self.step_run.body.addWidget(self.skip_check)
        self.strict_check = QCheckBox("Also delete lines that cross into an area\n(removes frame and table borders too)")
        self.step_run.body.addWidget(self.strict_check)
        self.run_btn = QPushButton("Mask drawings"); self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.clicked.connect(self.run_or_cancel)
        self.step_run.body.addSpacing(4)
        self.step_run.body.addWidget(self.run_btn)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.hide()
        self.step_run.body.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setObjectName("Log"); self.log.setReadOnly(True)
        self.log.setMinimumHeight(90); self.log.hide()
        self.step_run.body.addWidget(self.log)
        s.addWidget(self.step_run)
        s.addStretch()

        h.addWidget(side_scroll)

        # ---- workspace
        work = QWidget()
        w = QVBoxLayout(work); w.setContentsMargins(0, 0, 0, 0); w.setSpacing(0)

        bar = QWidget(); bar.setObjectName("Toolbar"); bar.setAttribute(Qt.WA_StyledBackground)
        b = QHBoxLayout(bar); b.setContentsMargins(16, 8, 12, 8); b.setSpacing(6)
        self.file_title = QLabel("No drawing open"); self.file_title.setObjectName("FileTitle")
        self.file_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        b.addWidget(self.file_title, 1)
        self.prev_file = QPushButton("Previous file"); self.prev_file.clicked.connect(lambda: self.step_file(-1))
        self.next_file = QPushButton("Next file"); self.next_file.clicked.connect(lambda: self.step_file(1))
        b.addWidget(self.prev_file); b.addWidget(self.next_file)
        b.addSpacing(14)
        self.prev_page = QPushButton("‹"); self.prev_page.setFixedWidth(34)
        self.prev_page.setToolTip("Previous page (Page Up)")
        self.prev_page.clicked.connect(lambda: self.step_page(-1))
        self.page_label = QLabel("Page –"); self.page_label.setObjectName("PageLabel")
        self.page_label.setMinimumWidth(86); self.page_label.setAlignment(Qt.AlignCenter)
        self.next_page = QPushButton("›"); self.next_page.setFixedWidth(34)
        self.next_page.setToolTip("Next page (Page Down)")
        self.next_page.clicked.connect(lambda: self.step_page(1))
        b.addWidget(self.prev_page); b.addWidget(self.page_label); b.addWidget(self.next_page)
        b.addSpacing(14)
        zo = QPushButton("−"); zo.setFixedWidth(34); zo.setToolTip("Zoom out (Ctrl + scroll)")
        zo.clicked.connect(lambda: self.view.zoom_by(1 / 1.25))
        self.zoom_label = QLabel("Fit"); self.zoom_label.setObjectName("PageLabel")
        self.zoom_label.setMinimumWidth(48); self.zoom_label.setAlignment(Qt.AlignCenter)
        zi = QPushButton("+"); zi.setFixedWidth(34); zi.setToolTip("Zoom in (Ctrl + scroll)")
        zi.clicked.connect(lambda: self.view.zoom_by(1.25))
        fit = QPushButton("Fit"); fit.setToolTip("Fit page (Ctrl+0)"); fit.clicked.connect(lambda: self.view.fit())
        for x in (zo, self.zoom_label, zi, fit):
            b.addWidget(x)
        w.addWidget(bar)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.view = PreviewView()
        self.view.areasChanged.connect(self._on_areas_changed)
        self.view.zoomChanged.connect(self._on_zoom)
        body.addWidget(self.view, 1)

        files_panel = QWidget(); files_panel.setObjectName("FilesPanel"); files_panel.setFixedWidth(250)
        files_panel.setAttribute(Qt.WA_StyledBackground)
        fp = QVBoxLayout(files_panel); fp.setContentsMargins(0, 12, 0, 0); fp.setSpacing(6)
        self.files_header = QLabel("Drawings"); self.files_header.setObjectName("StepTitle")
        self.files_header.setContentsMargins(14, 0, 14, 0)
        fp.addWidget(self.files_header)
        self.file_list = QListWidget(); self.file_list.setObjectName("FileList")
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        fp.addWidget(self.file_list, 1)
        body.addWidget(files_panel)
        w.addLayout(body, 1)
        h.addWidget(work, 1)

        # ---- status bar
        self.status_msg = QLabel("Choose a source folder to begin.")
        self.statusBar().addWidget(self.status_msg, 1)
        credit = QLabel(f"{APP_CREDIT}   v{APP_VERSION}")
        self.statusBar().addPermanentWidget(credit)
        self.statusBar().setSizeGripEnabled(False)

    def _build_actions(self):
        def act(text, keys, slot):
            a = QAction(text, self); a.setShortcuts(keys if isinstance(keys, list) else [keys])
            a.triggered.connect(slot); self.addAction(a); return a

        act("Open source folder", QKeySequence.Open, self.choose_source)
        act("Undo last area", QKeySequence.Undo, self.view.remove_last)
        act("Fit", QKeySequence("Ctrl+0"), self.view.fit)
        act("Zoom in", [QKeySequence.ZoomIn, QKeySequence("Ctrl+=")], lambda: self.view.zoom_by(1.25))
        act("Zoom out", QKeySequence.ZoomOut, lambda: self.view.zoom_by(1 / 1.25))
        act("Next page", QKeySequence(Qt.Key_PageDown), lambda: self.step_page(1))
        act("Previous page", QKeySequence(Qt.Key_PageUp), lambda: self.step_page(-1))
        act("Run", QKeySequence("Ctrl+Return"), self.run_or_cancel)

        help_menu = self.menuBar().addMenu("Help")
        about = QAction(f"About {APP_NAME}", self); about.triggered.connect(self.about)
        help_menu.addAction(about)
        keys = QAction("Keyboard shortcuts", self); keys.triggered.connect(self.shortcuts)
        help_menu.addAction(keys)

    def _on_zoom(self, z: float):
        self.zoom_label.setText(f"{round(z * self._render_zoom * 100)}%")

    # ============================================================== folders
    def choose_source(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the folder with your PDFs",
                                             self.src_edit.text() or str(Path.home()))
        if d:
            self.set_source(Path(d))

    def set_source(self, folder: Path):
        try:
            files = list_pdfs(folder)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Can't read that folder.\n\n{e}")
            return
        self.src_edit.setText(str(folder))
        if not self.out_edit.text():
            self.out_edit.setText(str(folder / "masked"))
        self.files = files
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for f in files:
            item = QListWidgetItem(f.name); item.setToolTip(str(f))
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)
        if files:
            self.file_list.setCurrentRow(0)
            self.status(f"{len(files)} PDFs found in {folder.name}.")
        else:
            self.current = None
            self.view.empty_text = "This folder has no PDF files. Choose another folder."
            self.view.set_page(None, [])
            self.status("No PDF files in this folder.")
        self._refresh()

    def choose_output(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to save the masked copies",
                                             self.out_edit.text() or self.src_edit.text() or str(Path.home()))
        if d:
            self.out_edit.setText(d)
            self._refresh()

    # ============================================================== preview
    def _on_file_selected(self, row: int):
        if 0 <= row < len(self.files):
            self.current = self.files[row]
            self.page_index = 0
            self.load_page()

    def step_file(self, d: int):
        r = self.file_list.currentRow() + d
        if 0 <= r < self.file_list.count():
            self.file_list.setCurrentRow(r)

    def step_page(self, d: int):
        n = self.page_index + d
        if self.current and 0 <= n < self.page_count:
            self.page_index = n
            self.load_page()

    def load_page(self):
        if not self.current:
            return
        try:
            with pymupdf.open(self.current) as doc:
                if doc.needs_pass:
                    raise ValueError("This file is password-protected.")
                self.page_count = len(doc)
                page = doc[self.page_index]
                self.ref_landscape = is_landscape(page)
                zoom = min(3.0, PREVIEW_PX / max(page.rect.width, page.rect.height))
                self._render_zoom = zoom
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        except Exception as e:  # noqa: BLE001
            self.view.empty_text = f"Can't open {self.current.name}: {e}"
            self.view.set_page(None, [])
            self.status(f"Can't open {self.current.name}.")
            self._refresh()
            return
        self.view.set_page(img, self.areas)
        self.file_title.setText(self.current.name)
        self._refresh()

    # ================================================================ areas
    def _on_areas_changed(self):
        self.areas = self.view.areas()
        self._refresh()

    def remove_area(self):
        if not self.view.remove_selected():
            row = self.area_list.currentRow()
            if row >= 0:
                self.view.remove_index(row)

    def clear_areas(self):
        if self.areas and QMessageBox.question(self, APP_NAME, "Remove all mask areas?") == QMessageBox.Yes:
            self.view.clear_areas()

    def save_preset(self):
        if not self.areas:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save areas as preset", "mask-preset.json", "Preset (*.json)")
        if path:
            Preset(self.areas, self.fill_combo.currentText().lower(), self.skip_check.isChecked()).save(Path(path))
            self.status(f"Preset saved: {Path(path).name}")

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load preset", "", "Preset (*.json)")
        if not path:
            return
        try:
            preset = Preset.load(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"That file isn't a valid preset.\n\n{e}")
            return
        self.areas = preset.areas
        self.fill_combo.setCurrentText(preset.fill.capitalize())
        self.skip_check.setChecked(preset.skip_other_orientation)
        self.load_page() if self.current else self._refresh()
        self.status(f"Preset loaded: {len(self.areas)} areas.")

    # ================================================================== run
    def run_or_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.run_btn.setEnabled(False)
            self.run_btn.setText("Stopping after this file")
            return
        if not self.run_btn.isEnabled():
            return
        out = Path(self.out_edit.text())
        src = Path(self.src_edit.text())
        if out.resolve() == src.resolve():
            QMessageBox.warning(self, APP_NAME, "Save the masked copies to a different folder "
                                                 "so your originals are kept.")
            return
        existing = [f for f in self.files if (out / f.name).exists()]
        if existing and QMessageBox.question(
                self, APP_NAME, f"{len(existing)} files already exist in the output folder. Replace them?"
        ) != QMessageBox.Yes:
            return

        opts = Options(fill=FILLS[self.fill_combo.currentText()],
                       skip_other_orientation=self.skip_check.isChecked(),
                       reference_landscape=self.ref_landscape,
                       strict_lines=self.strict_check.isChecked())
        self.worker = MaskWorker(list(self.files), out, list(self.areas), opts)
        self.worker.progress.connect(self._on_progress)
        self.worker.fileDone.connect(self._on_file_done)
        self.worker.allDone.connect(self._on_all_done)
        self.log.clear(); self.log.show()
        self.progress.setRange(0, len(self.files)); self.progress.setValue(0); self.progress.show()
        self.run_btn.setText("Cancel")
        self._set_editing(False)
        self.worker.start()

    def _on_progress(self, done: int, total: int):
        self.progress.setValue(done)
        self.status(f"Masking {done} of {total}")

    def _on_file_done(self, name: str, ok: bool, msg: str):
        self.log.appendPlainText(f"{'Done' if ok else 'Failed'}   {name}   {msg}")

    def _on_all_done(self, ok: int, failed: int, cancelled: bool):
        self._set_editing(True)
        self.run_btn.setEnabled(True)
        self._refresh()
        out = self.out_edit.text()
        summary = f"{ok} drawings masked" + (f", {failed} failed" if failed else "")
        if cancelled:
            summary = "Stopped. " + summary
        self.status(summary + ".")
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText(summary + ".")
        box.setInformativeText("Spot-check one copy: search or select text inside a masked area "
                               "to confirm nothing is left underneath."
                               + ("\n\nFailed files are listed in the log." if failed else ""))
        open_btn = box.addButton("Open output folder", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(out))

    def _set_editing(self, enabled: bool):
        for wdg in (self.step_src, self.step_out, self.step_areas, self.fill_combo,
                    self.skip_check, self.strict_check, self.file_list, self.view):
            wdg.setEnabled(enabled)

    # ============================================================== helpers
    def _refresh(self):
        self.area_list.blockSignals(True)
        self.area_list.clear()
        for i, a in enumerate(self.areas, 1):
            self.area_list.addItem(
                f"Area {i}    {a.x0*100:.0f}–{a.x1*100:.0f}% across, {a.y0*100:.0f}–{a.y1*100:.0f}% down")
        self.area_list.blockSignals(False)
        if not self.areas:
            it = QListWidgetItem("No areas yet"); it.setFlags(Qt.NoItemFlags)
            self.area_list.addItem(it)

        self.files_header.setText(f"Drawings in folder ({len(self.files)})" if self.files else "Drawings in folder")
        has_src, has_out, has_areas = bool(self.files), bool(self.out_edit.text()), bool(self.areas)
        self.step_src.set_done(has_src)
        self.step_out.set_done(has_out)
        self.step_areas.set_done(has_areas)
        for b in (self.btn_remove, self.btn_clear_all, self.btn_save_preset):
            b.setEnabled(has_areas)

        running = bool(self.worker and self.worker.isRunning())
        if not running:
            n = len(self.files)
            self.run_btn.setText(f"Mask {n} drawing{'s' if n != 1 else ''}" if n else "Mask drawings")
            self.run_btn.setEnabled(has_src and has_out and has_areas)
            if has_src and not has_areas:
                self.run_btn.setToolTip("Mark at least one area on the drawing first.")
            else:
                self.run_btn.setToolTip("Ctrl+Enter")

        self.page_label.setText(f"Page {self.page_index + 1} of {self.page_count}" if self.current else "Page –")
        self.prev_page.setEnabled(bool(self.current) and self.page_index > 0)
        self.next_page.setEnabled(bool(self.current) and self.page_index < self.page_count - 1)
        row = self.file_list.currentRow()
        self.prev_file.setEnabled(row > 0)
        self.next_file.setEnabled(0 <= row < self.file_list.count() - 1)

    def status(self, text: str):
        self.status_msg.setText(text)

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {APP_VERSION}<br>by {APP_AUTHOR}<br><br>"
                          "Permanently removes text, lines and images inside the areas you mark, "
                          "on every page of every PDF in a folder. Everything runs on this computer; "
                          "no files are uploaded.<br><br>Built with PySide6 and PyMuPDF. "
                          "Typeface: IBM Plex Sans (SIL Open Font License).")

    def shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts",
                                "Ctrl+O    Open source folder\n"
                                "Ctrl+Z    Remove the last area\n"
                                "Delete    Remove the selected area\n"
                                "Ctrl+scroll    Zoom\n"
                                "Ctrl+0    Fit page\n"
                                "Middle-drag or Alt+drag    Pan\n"
                                "Page Up / Page Down    Change page\n"
                                "Ctrl+Enter    Mask all drawings")

    # ============================================================ drag/drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = Path(e.mimeData().urls()[0].toLocalFile())
        self.set_source(p if p.is_dir() else p.parent)

    # ============================================================= settings
    def _restore(self):
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        raw = self.settings.value("areas", "")
        if raw:
            try:
                import json
                self.areas = [Area(**a).normalized() for a in json.loads(raw)]
            except Exception:  # noqa: BLE001
                self.areas = []
        self.fill_combo.setCurrentText(self.settings.value("fill", "White"))
        out = self.settings.value("output", "")
        src = self.settings.value("source", "")
        if src and Path(src).is_dir():
            self.set_source(Path(src))
        if out:
            self.out_edit.setText(out)

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME, "Masking is still running. Stop and quit?") != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel(); self.worker.wait(5000)
        import json
        from dataclasses import asdict
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("areas", json.dumps([asdict(a) for a in self.areas]))
        self.settings.setValue("fill", self.fill_combo.currentText())
        self.settings.setValue("source", self.src_edit.text())
        self.settings.setValue("output", self.out_edit.text())
        super().closeEvent(e)
