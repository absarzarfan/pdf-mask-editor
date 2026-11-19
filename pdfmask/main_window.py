"""Main application window."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf
from PySide6.QtCore import QDir, QPointF, QProcess, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QImage, QKeySequence, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import APP_AUTHOR, APP_CREDIT, APP_NAME, APP_VERSION
from .canvas import PreviewView
from .core import (Area, FileResult, Layout, LayoutError, Options, PageRangeError, Preset,
                   default_output_path, format_page_ranges, is_landscape, list_pdfs,
                   next_layout_name, parse_page_ranges, resolve_layouts, same_file, split_layout)
from .pagestrip import PageStrip
from .resources import app_icon
from .worker import MaskWorker

FILLS = {"White": (1.0, 1.0, 1.0), "Black": (0.0, 0.0, 0.0)}
PREVIEW_PX = 2200
SIDEBAR_W = 360


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


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


class Step(QWidget):
    """Numbered sidebar section. The steps really are a sequence, so they are numbered."""

    def __init__(self, number: int, title: str, hint: str = ""):
        super().__init__()
        self.number = number
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        head = QHBoxLayout(); head.setSpacing(10)
        self.badge = QLabel(str(number)); self.badge.setObjectName("StepNumber")
        self.title = QLabel(title); self.title.setObjectName("StepTitle")
        head.addWidget(self.badge); head.addWidget(self.title); head.addStretch()
        lay.addLayout(head)
        self.hint = QLabel(hint); self.hint.setObjectName("Hint"); self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self.body = QVBoxLayout(); self.body.setSpacing(6)
        lay.addLayout(self.body)

    def set_text(self, title: str, hint: str = ""):
        self.title.setText(title)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def set_done(self, done: bool):
        if done:
            self.badge.setText("")
            self.badge.setPixmap(_check_pixmap(self.badge.devicePixelRatioF()))
        else:
            self.badge.setPixmap(QPixmap())
            self.badge.setText(str(self.number))


class _RowEdit(QLineEdit):
    focused = Signal()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focused.emit()


class LayoutRow(QWidget):
    """One row of the layout list: name, editable page range, area count."""
    nameEdited = Signal(str)
    rangeEdited = Signal(str)
    focused = Signal()

    def __init__(self, layout: Layout):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(4, 3, 8, 3); h.setSpacing(6)
        self.name_edit = _RowEdit(layout.name); self.name_edit.setObjectName("RowName")
        self.name_edit.setToolTip("Layout name")
        self.range_edit = _RowEdit(layout.ranges); self.range_edit.setObjectName("RowRange")
        self.range_edit.setPlaceholderText("e.g. 1-10, 15, 20-end")
        self.range_edit.setToolTip("Pages this layout covers, for example 1-30, 35-end")
        self.range_edit.setFixedWidth(116)
        self.count = QLabel(); self.count.setObjectName("RowCount")
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter); self.count.setMinimumWidth(50)
        h.addWidget(self.name_edit, 1); h.addWidget(self.range_edit); h.addWidget(self.count)
        self.name_edit.editingFinished.connect(lambda: self.nameEdited.emit(self.name_edit.text()))
        self.range_edit.editingFinished.connect(lambda: self.rangeEdited.emit(self.range_edit.text()))
        self.name_edit.focused.connect(self.focused)
        self.range_edit.focused.connect(self.focused)
        self.set_count(len(layout.areas))

    def set_count(self, n: int):
        self.count.setText(_plural(n, "area"))


# the only settings that persist; every other key is deleted on startup
KEPT_KEYS = ("geometry", "mode")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)

        self.settings = QSettings("absarz software", "PDF Mask Editor")
        self.mode = "file"                      # "file" (default) or "folder"
        # per-mode picks, so switching the mode switch doesn't lose them
        self.file_src: Path | None = None
        self.file_out: Path | None = None
        self.dir_src = ""
        self.dir_out = ""

        self.files: list[Path] = []             # folder mode
        self.current: Path | None = None        # the file being previewed
        self.page_index = 0
        self.page_count = 0
        self.layouts: list[Layout] = []
        self.ref_landscape: bool | None = None  # orientation of the previewed page
        self.worker: MaskWorker | None = None
        self.results: list[FileResult] = []
        self.last_error = ""
        self._render_zoom = 1.0
        self._owner: dict[int, int] = {}        # 1-based page -> layout index
        self._clash: set[int] = set()
        self._problem = ""                      # why the layouts can't run (overlap, bad range)
        self._row_error = ""                    # last rejected range edit
        self._syncing = False

        self._build_ui()
        self._build_actions()
        self._restore()
        self._apply_mode()
        self._refresh()

    # ================================================================ layout
    def _build_ui(self):
        root = QWidget(); root.setObjectName("Root")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.setCentralWidget(root)

        # ---- sidebar: scrolling steps, run button pinned underneath
        side_col = QWidget(); side_col.setFixedWidth(SIDEBAR_W)
        sc = QVBoxLayout(side_col); sc.setContentsMargins(0, 0, 0, 0); sc.setSpacing(0)

        side = QWidget(); side.setObjectName("Sidebar")
        side.setAttribute(Qt.WA_StyledBackground)
        side_scroll = QScrollArea(); side_scroll.setWidget(side); side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s = QVBoxLayout(side); s.setContentsMargins(22, 18, 22, 16); s.setSpacing(14)

        brand = QVBoxLayout(); brand.setSpacing(0)
        name = QLabel(APP_NAME); name.setStyleSheet("font-size: 15pt; font-weight: 700;")
        by = QLabel(f"by {APP_AUTHOR}"); by.setObjectName("Hint")
        brand.addWidget(name); brand.addWidget(by)
        s.addLayout(brand)

        # mode switch
        seg = QHBoxLayout(); seg.setSpacing(0)
        self.mode_group = QButtonGroup(self); self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for key, text, side_name, tip in (("file", "One file", "left", "Mask one PDF into one masked PDF (Ctrl+1)"),
                                          ("folder", "Folder", "right", "Mask every PDF in a folder (Ctrl+2)")):
            b = QPushButton(text); b.setObjectName("Segment"); b.setProperty("side", side_name)
            b.setCheckable(True); b.setToolTip(tip); b.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(b); self.mode_buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            seg.addWidget(b, 1)
        s.addLayout(seg)
        s.addWidget(_divider())

        # step 1
        self.step_src = Step(1, "Drawing file", "Your original PDF stays untouched. You can also drop a PDF on the window.")
        row = QHBoxLayout()
        self.src_edit = QLineEdit(); self.src_edit.setReadOnly(True)
        self.browse_src = QPushButton("Browse"); self.browse_src.clicked.connect(self.choose_source)
        row.addWidget(self.src_edit, 1); row.addWidget(self.browse_src)
        self.step_src.body.addLayout(row)
        s.addWidget(self.step_src)

        # step 2
        self.step_out = Step(2, "Save masked copy as")
        row = QHBoxLayout()
        self.out_edit = QLineEdit(); self.out_edit.setReadOnly(True)
        self.browse_out = QPushButton("Browse"); self.browse_out.clicked.connect(self.choose_output)
        row.addWidget(self.out_edit, 1); row.addWidget(self.browse_out)
        self.step_out.body.addLayout(row)
        s.addWidget(self.step_out)

        # step 3
        self.step_areas = Step(3, "Mark the areas to hide",
                               "Drag on the drawing to add an area. It goes into the layout that covers "
                               "the current page. Where the positions change, start a new layout.")
        self.layout_list = QListWidget(); self.layout_list.setObjectName("LayoutList")
        self.layout_list.setFixedHeight(128)
        self.layout_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.layout_list.currentRowChanged.connect(self._on_layout_row)
        self.step_areas.body.addWidget(self.layout_list)
        self.layout_msg = QLabel(); self.layout_msg.setObjectName("Hint"); self.layout_msg.setWordWrap(True)
        self.layout_msg.setStyleSheet("font-weight: 600;")
        self.layout_msg.hide()
        self.step_areas.body.addWidget(self.layout_msg)
        row = QHBoxLayout(); row.setSpacing(6)
        self.btn_add_layout = QPushButton("Add layout"); self.btn_add_layout.clicked.connect(self.add_layout)
        self.btn_duplicate = QPushButton("Duplicate"); self.btn_duplicate.clicked.connect(self.duplicate_layout)
        self.btn_remove_layout = QPushButton("Remove"); self.btn_remove_layout.clicked.connect(self.remove_layout)
        for b in (self.btn_add_layout, self.btn_duplicate, self.btn_remove_layout):
            b.setToolTip("Layouts group pages that have the mask areas in the same place")
            row.addWidget(b)
        self.step_areas.body.addLayout(row)
        self.btn_split = QPushButton("Start a new layout at this page")
        self.btn_split.setToolTip("Pages from here on move to a new layout with a copy of these areas (Ctrl+L)")
        self.btn_split.clicked.connect(self.split_here)
        self.step_areas.body.addWidget(self.btn_split)

        self.areas_title = QLabel("Areas on this page"); self.areas_title.setObjectName("Hint")
        self.areas_title.setContentsMargins(0, 6, 0, 0)
        self.step_areas.body.addWidget(self.areas_title)
        self.area_list = QListWidget(); self.area_list.setFixedHeight(78)
        self.area_list.currentRowChanged.connect(lambda r: self.view.select_index(r))
        self.step_areas.body.addWidget(self.area_list)
        for pair in ((("Remove area", self.remove_area), ("Clear areas", self.clear_areas)),
                     (("Load preset", self.load_preset), ("Save preset", self.save_preset))):
            row = QHBoxLayout(); row.setSpacing(4)
            for text, slot in pair:
                btn = QPushButton(text); btn.setObjectName("Ghost"); btn.clicked.connect(slot)
                row.addWidget(btn)
                setattr(self, "btn_" + text.lower().replace(" ", "_"), btn)
            row.addStretch()
            self.step_areas.body.addLayout(row)
        s.addWidget(self.step_areas)

        # step 4
        self.step_run = Step(4, "Mask this drawing")
        row = QHBoxLayout()
        lbl = QLabel("Cover with"); lbl.setObjectName("Hint")
        self.fill_combo = QComboBox(); self.fill_combo.addItems(list(FILLS))
        row.addWidget(lbl); row.addWidget(self.fill_combo); row.addStretch()
        self.step_run.body.addLayout(row)
        self.skip_check = QCheckBox("Skip pages turned the other way\nfrom the page a layout was drawn on")
        self.skip_check.setChecked(True)
        self.step_run.body.addWidget(self.skip_check)
        self.strict_check = QCheckBox("Also delete lines that cross into an area\n(removes frame and table borders too)")
        self.step_run.body.addWidget(self.strict_check)
        s.addWidget(self.step_run)
        s.addStretch()
        sc.addWidget(side_scroll, 1)

        footer = QWidget(); footer.setObjectName("SidebarFooter"); footer.setAttribute(Qt.WA_StyledBackground)
        f = QVBoxLayout(footer); f.setContentsMargins(22, 12, 22, 12); f.setSpacing(8)
        self.run_btn = QPushButton("Mask this drawing"); self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.clicked.connect(self.run_or_cancel)
        f.addWidget(self.run_btn)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.hide()
        f.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setObjectName("Log"); self.log.setReadOnly(True)
        self.log.setFixedHeight(70); self.log.hide()
        f.addWidget(self.log)
        sc.addWidget(footer)
        h.addWidget(side_col)

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

        # page strip
        strip_bar = QWidget(); strip_bar.setObjectName("StripBar"); strip_bar.setAttribute(Qt.WA_StyledBackground)
        sb = QVBoxLayout(strip_bar); sb.setContentsMargins(16, 8, 16, 6); sb.setSpacing(4)
        self.strip = PageStrip()
        self.strip.pageClicked.connect(self.goto_page)
        self.strip.pageHovered.connect(self._on_strip_hover)
        sb.addWidget(self.strip)
        self.strip_caption = QLabel(""); self.strip_caption.setObjectName("StripCaption")
        sb.addWidget(self.strip_caption)
        self.strip_bar = strip_bar
        w.addWidget(strip_bar)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.view = PreviewView()
        self.view.areasChanged.connect(self._on_areas_changed)
        self.view.zoomChanged.connect(self._on_zoom)
        body.addWidget(self.view, 1)

        self.files_panel = QWidget(); self.files_panel.setObjectName("FilesPanel")
        self.files_panel.setFixedWidth(230)
        self.files_panel.setAttribute(Qt.WA_StyledBackground)
        fp = QVBoxLayout(self.files_panel); fp.setContentsMargins(0, 12, 0, 0); fp.setSpacing(6)
        self.files_header = QLabel("Drawings"); self.files_header.setObjectName("StepTitle")
        self.files_header.setContentsMargins(14, 0, 14, 0)
        fp.addWidget(self.files_header)
        self.file_list = QListWidget(); self.file_list.setObjectName("FileList")
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        fp.addWidget(self.file_list, 1)
        body.addWidget(self.files_panel)
        w.addLayout(body, 1)
        h.addWidget(work, 1)

        # ---- status bar
        self.status_msg = QLabel("Choose a drawing file to begin.")
        self.statusBar().addWidget(self.status_msg, 1)
        credit = QLabel(f"{APP_CREDIT}   v{APP_VERSION}")
        self.statusBar().addPermanentWidget(credit)
        self.statusBar().setSizeGripEnabled(False)

    def _build_actions(self):
        def act(text, keys, slot):
            a = QAction(text, self); a.setShortcuts(keys if isinstance(keys, list) else [keys])
            a.triggered.connect(slot); self.addAction(a); return a

        act("Open drawing file or folder", QKeySequence.Open, self.choose_source)
        act("Save masked copy as", QKeySequence.Save, self.choose_output)
        act("One file mode", QKeySequence("Ctrl+1"), lambda: self.set_mode("file"))
        act("Folder mode", QKeySequence("Ctrl+2"), lambda: self.set_mode("folder"))
        act("Undo last area", QKeySequence.Undo, self.view.remove_last)
        act("Start a new layout at this page", QKeySequence("Ctrl+L"), self.split_here)
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

    # ================================================================== mode
    def set_mode(self, mode: str):
        if self.worker and self.worker.isRunning():
            return
        if mode == self.mode and self.mode_buttons[mode].isChecked():
            return
        self.mode = mode
        self._apply_mode()
        self._refresh()

    def _apply_mode(self):
        single = self.mode == "file"
        self.mode_buttons[self.mode].setChecked(True)
        self.files_panel.setVisible(not single)
        self.prev_file.setVisible(not single)
        self.next_file.setVisible(not single)
        if single:
            self.step_src.set_text("Drawing file", "Your original PDF stays untouched. "
                                                   "You can also drop a PDF on the window.")
            self.step_out.set_text("Save masked copy as")
            self.step_run.set_text("Mask this drawing")
            self.src_edit.setPlaceholderText("No file chosen")
            self.out_edit.setPlaceholderText("Same folder, <name>_masked.pdf")
            self.src_edit.setText(str(self.file_src) if self.file_src else "")
            self.out_edit.setText(str(self.file_out) if self.file_out else "")
            self.view.empty_text = "Choose a drawing file to preview it"
        else:
            self.step_src.set_text("Source folder", "Your original PDFs stay untouched.")
            self.step_out.set_text("Save masked copies to")
            self.step_run.set_text("Mask every drawing")
            self.src_edit.setPlaceholderText("No folder chosen")
            self.out_edit.setPlaceholderText("No folder chosen")
            self.src_edit.setText(self.dir_src)
            self.out_edit.setText(self.dir_out)
            self.view.empty_text = "Choose a source folder to preview a drawing"
        if single and not self.file_src:
            self.status("Choose a drawing file to begin.")
        elif not single and not self.files:
            self.status("Choose a source folder to begin.")
        self._reload_current()

    def _reload_current(self):
        """Show whatever file belongs to the current mode."""
        self.page_index = 0
        if self.mode == "file":
            self.current = self.file_src
        else:
            row = self.file_list.currentRow()
            self.current = self.files[row] if 0 <= row < len(self.files) else None
        if self.current:
            self.load_page()
        else:
            self.page_count = 0
            self.ref_landscape = None
            self.file_title.setText("No drawing open")
            self.view.set_page(None, [])
            self._after_layout_change()

    # ================================================================ sources
    def choose_source(self):
        if self.mode == "file":
            start = str(self.file_src.parent) if self.file_src else str(Path.home())
            path, _ = QFileDialog.getOpenFileName(self, "Choose the drawing file", start, "PDF files (*.pdf)")
            if path:
                self.set_source_file(Path(path))
        else:
            d = QFileDialog.getExistingDirectory(self, "Choose the folder with your PDFs",
                                                 self.dir_src or str(Path.home()))
            if d:
                self.set_source_folder(Path(d))

    def choose_output(self):
        if self.mode == "file":
            if not self.file_src:
                self.status("Choose a drawing file first.")
                return
            start = self.file_out or default_output_path(self.file_src)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save masked copy as", str(start), "PDF files (*.pdf)",
                options=QFileDialog.DontConfirmOverwrite)  # asked once, when masking starts
            if path:
                p = Path(path)
                if p.suffix.lower() != ".pdf":
                    p = p.with_name(p.name + ".pdf")
                self.file_out = p
                self.out_edit.setText(str(p))
                self._refresh()
        else:
            d = QFileDialog.getExistingDirectory(self, "Choose where to save the masked copies",
                                                 self.dir_out or self.dir_src or str(Path.home()))
            if d:
                self.dir_out = d
                self.out_edit.setText(d)
                self._refresh()

    def set_source_file(self, path: Path):
        try:
            img, count, landscape, zoom = self._render(path, 0)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Can't open that file.\n\n{e}")
            return
        self.mode = "file"
        self.file_src = path
        self.file_out = default_output_path(path)
        self.mode_buttons["file"].setChecked(True)
        self._apply_mode()   # reloads page 1 of the new file
        self.status(f"{path.name} opened, {_plural(count, 'page')}.")
        self._refresh()

    def set_source_folder(self, folder: Path):
        try:
            files = list_pdfs(folder)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Can't read that folder.\n\n{e}")
            return
        self.mode = "folder"
        self.dir_src = str(folder)
        if not self.dir_out:
            self.dir_out = str(folder / "masked")
        self.files = files
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for f in files:
            item = QListWidgetItem(f.name); item.setToolTip(str(f))
            self.file_list.addItem(item)
        self.file_list.setCurrentRow(0 if files else -1)
        self.file_list.blockSignals(False)
        self.mode_buttons["folder"].setChecked(True)
        self._apply_mode()
        self.status(f"{_plural(len(files), 'PDF')} found in {folder.name}." if files
                    else "No PDF files in this folder.")
        if not files:
            self.view.empty_text = "This folder has no PDF files. Choose another folder."
            self.view.viewport().update()
        self._refresh()

    # ============================================================== preview
    def _on_file_selected(self, row: int):
        if self.mode == "folder" and 0 <= row < len(self.files):
            self.current = self.files[row]
            self.page_index = 0
            self.load_page()

    def step_file(self, d: int):
        r = self.file_list.currentRow() + d
        if 0 <= r < self.file_list.count():
            self.file_list.setCurrentRow(r)

    def step_page(self, d: int):
        self.goto_page(self.page_index + d)

    def goto_page(self, index: int):
        if self.current and 0 <= index < self.page_count and index != self.page_index:
            self.page_index = index
            self.load_page()

    def _render(self, path: Path, index: int):
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise ValueError("This file is password-protected.")
            count = len(doc)
            if count == 0:
                raise ValueError("This file has no pages.")
            page = doc[min(index, count - 1)]
            zoom = min(3.0, PREVIEW_PX / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            return img, count, is_landscape(page), zoom

    def load_page(self):
        if not self.current:
            return
        try:
            img, count, landscape, zoom = self._render(self.current, self.page_index)
        except Exception as e:  # noqa: BLE001
            self.page_count = 0
            self.view.empty_text = f"Can't open {self.current.name}: {e}"
            self.view.set_page(None, [])
            self.status(f"Can't open {self.current.name}.")
            self._after_layout_change()
            return
        self.page_count = count
        self.page_index = min(self.page_index, count - 1)
        self.ref_landscape = landscape
        self._render_zoom = zoom
        self._resolve()
        self.view.set_page(img, self._areas_for_page())
        self.file_title.setText(self.current.name)
        self._after_layout_change()

    # ================================================================ layouts
    def _resolve(self):
        """Work out which layout owns each page, plus any problem that blocks a run."""
        owner: dict[int, int] = {}
        clash: set[int] = set()
        n = self.page_count
        for i, layout in enumerate(self.layouts):
            try:
                pages = layout.pages(n)
            except PageRangeError:
                continue
            for p in pages:
                if p in owner:
                    clash.add(p)
                else:
                    owner[p] = i
        self._owner, self._clash = owner, clash
        self._problem = ""
        if n:
            try:
                resolve_layouts(self.layouts, n)
            except LayoutError as e:
                self._problem = str(e)

    def _page_layout(self, page_index: int | None = None) -> int | None:
        page = (self.page_index if page_index is None else page_index) + 1
        return self._owner.get(page)

    def _areas_for_page(self) -> list[Area]:
        idx = self._page_layout()
        return list(self.layouts[idx].areas) if idx is not None else []

    def _after_layout_change(self, rebuild: bool = False, refresh_areas: bool = False,
                             select: int | None = None):
        """Bring every widget that shows layouts back in line with self.layouts.

        Rows are only rebuilt when asked or when the list is out of step, because
        rebuilding while a row's field has focus would delete the field under the user.
        """
        self._resolve()
        if refresh_areas and self.view.page_item is not None:
            self.view.set_areas(self._areas_for_page())
        if rebuild or self._rows_stale():
            self._rebuild_layout_list()
        else:
            self._update_row_counts()
        if select is None:
            self._sync_layout_selection()
        else:
            self.layout_list.setCurrentRow(select)
        self._refresh()

    def _rows_stale(self) -> bool:
        return sum(1 for _ in self._row_widgets()) != len(self.layouts)

    def _rebuild_layout_list(self):
        self._syncing = True
        self.layout_list.blockSignals(True)
        self.layout_list.clear()
        for layout in self.layouts:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 36))
            row = LayoutRow(layout)
            row.nameEdited.connect(lambda text, lay=layout: self._rename_layout(lay, text))
            row.rangeEdited.connect(lambda text, lay=layout: self._edit_ranges(lay, text))
            row.focused.connect(lambda lay=layout: self._select_layout(lay))
            self.layout_list.addItem(item)
            self.layout_list.setItemWidget(item, row)
        if not self.layouts:
            it = QListWidgetItem("No layouts yet. Draw an area to create one.")
            it.setFlags(Qt.NoItemFlags)
            self.layout_list.addItem(it)
        self.layout_list.blockSignals(False)
        self._syncing = False

    def _row_widgets(self):
        for i in range(len(self.layouts)):
            w = self.layout_list.itemWidget(self.layout_list.item(i)) if i < self.layout_list.count() else None
            if isinstance(w, LayoutRow):
                yield i, w

    def _update_row_counts(self):
        for i, row in self._row_widgets():
            row.set_count(len(self.layouts[i].areas))

    def _sync_layout_selection(self):
        idx = self._page_layout()
        self._syncing = True
        self.layout_list.blockSignals(True)
        self.layout_list.setCurrentRow(idx if idx is not None else -1)
        if idx is None:
            self.layout_list.clearSelection()
        self.layout_list.blockSignals(False)
        self._syncing = False

    def _index_of(self, layout: Layout) -> int:
        return next(i for i, lay in enumerate(self.layouts) if lay is layout)

    def _select_layout(self, layout: Layout):
        """A row's field got focus: select that row (and show its pages)."""
        self.layout_list.setCurrentRow(self._index_of(layout))

    def _on_layout_row(self, row: int):
        if self._syncing or not (0 <= row < len(self.layouts)) or not self.page_count:
            return
        pages = sorted(self.layouts[row].pages(self.page_count)) if not self._range_error(self.layouts[row]) else []
        if pages and (self.page_index + 1) not in pages:
            self.goto_page(pages[0] - 1)

    def _range_error(self, layout: Layout) -> str:
        try:
            layout.pages(self.page_count)
        except PageRangeError as e:
            return str(e)
        return ""

    def _row_for(self, layout: Layout) -> LayoutRow | None:
        i = self._index_of(layout)
        return next((row for j, row in self._row_widgets() if j == i), None)

    def _rename_layout(self, layout: Layout, text: str):
        text = text.strip()
        if text and text != layout.name:
            layout.name = text
            self._after_layout_change()
        elif not text and (row := self._row_for(layout)):
            row.name_edit.setText(layout.name)

    def _edit_ranges(self, layout: Layout, text: str):
        text = text.strip()
        if text == layout.ranges:
            return
        if text and self.page_count:
            try:
                parse_page_ranges(text, self.page_count)
            except PageRangeError as e:
                self._row_error = f"{layout.name}: {e}"
                if row := self._row_for(layout):
                    row.range_edit.setText(layout.ranges)   # put the last valid pages back
                self.status(self._row_error)
                self._refresh()
                return
        self._row_error = ""
        layout.ranges = text
        self._after_layout_change(refresh_areas=True)

    def _first_uncovered(self) -> int | None:
        return next((p for p in range(1, self.page_count + 1) if p not in self._owner), None)

    def add_layout(self):
        if not self.page_count:
            return
        page = self._first_uncovered()
        self.layouts.append(Layout(next_layout_name(self.layouts), str(page) if page else "",
                                   [], self.ref_landscape))
        self.status("New layout added." if page else
                    "Every page already belongs to a layout. Type the pages for the new layout.")
        self._row_error = ""
        self._after_layout_change(rebuild=True, refresh_areas=True, select=len(self.layouts) - 1)

    def _selected_layout_index(self) -> int | None:
        row = self.layout_list.currentRow()
        if 0 <= row < len(self.layouts):
            return row
        return self._page_layout()

    def duplicate_layout(self):
        idx = self._selected_layout_index()
        if idx is None or not self.page_count:
            return
        src = self.layouts[idx]
        page = self._first_uncovered()
        copy = Layout(next_layout_name(self.layouts), str(page) if page else "",
                      list(src.areas), src.reference_landscape)
        self.layouts.insert(idx + 1, copy)
        self.status("Layout duplicated onto the first free page." if page else
                    "Layout duplicated. Type the pages it should cover.")
        self._row_error = ""
        self._after_layout_change(rebuild=True, refresh_areas=True, select=idx + 1)

    def remove_layout(self):
        idx = self._selected_layout_index()
        if idx is None:
            return
        gone = self.layouts.pop(idx)
        self.status(f"{gone.name} removed. Its pages are now left unchanged.")
        self._row_error = ""
        self._after_layout_change(rebuild=True, refresh_areas=True)

    def split_here(self):
        if not self.page_count:
            return
        idx = self._page_layout()
        if idx is None:
            self.status("This page has no layout yet. Draw an area on it to create one.")
            return
        try:
            self.layouts = split_layout(self.layouts, idx, self.page_index + 1, self.page_count)
        except ValueError as e:
            self.status(str(e))
            return
        new = self.layouts[idx + 1]
        self.status(f"{new.name} starts here (pages {new.ranges}). Adjust its areas on the drawing.")
        self._row_error = ""
        self._after_layout_change(rebuild=True, refresh_areas=True)

    # ================================================================ areas
    def _on_areas_changed(self):
        areas = self.view.areas()
        idx = self._page_layout()
        rebuild = False
        if idx is None:
            if not areas:
                return
            if not self.layouts:
                # first layout of the session: cover the whole file, the common case
                self.layouts.append(Layout("Layout 1", "1-end", areas, self.ref_landscape))
                self.status("Layout 1 covers every page. Where the positions change, "
                            "use \"Start a new layout at this page\".")
            else:
                name = next_layout_name(self.layouts)
                self.layouts.append(Layout(name, str(self.page_index + 1), areas, self.ref_landscape))
                self.status(f"{name} created for page {self.page_index + 1}.")
            rebuild = True
        else:
            layout = self.layouts[idx]
            layout.areas = areas
            if layout.reference_landscape is None:
                layout.reference_landscape = self.ref_landscape
        self._after_layout_change(rebuild=rebuild)

    def remove_area(self):
        if not self.view.remove_selected():
            row = self.area_list.currentRow()
            if row >= 0:
                self.view.remove_index(row)

    def clear_areas(self):
        if self.view.items_ and QMessageBox.question(
                self, APP_NAME, "Remove all areas from this layout?") == QMessageBox.Yes:
            self.view.clear_areas()

    def _has_areas(self) -> bool:
        return any(lay.areas for lay in self.layouts)

    def save_preset(self):
        if not self._has_areas():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save layouts as preset", "mask-preset.json", "Preset (*.json)")
        if path:
            Preset(self.layouts, self.fill_combo.currentText().lower(), self.skip_check.isChecked()).save(Path(path))
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
        self.layouts = preset.layouts
        self.fill_combo.setCurrentText(preset.fill.capitalize())
        self.skip_check.setChecked(preset.skip_other_orientation)
        self._row_error = ""
        self._after_layout_change(rebuild=True, refresh_areas=True)
        self.status(f"Preset loaded: {_plural(len(self.layouts), 'layout')}.")

    # ================================================================== run
    def run_or_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.run_btn.setEnabled(False)
            self.run_btn.setText("Stopping" if self.mode == "file" else "Stopping after this file")
            return
        if not self.run_btn.isEnabled():
            return
        single = self.mode == "file"

        # where to write, and never over the source
        if single:
            src, out = self.file_src, self.file_out
            if not src or not out:
                return
            if out.suffix.lower() != ".pdf":
                out = out.with_name(out.name + ".pdf")
            if same_file(src, out):
                QMessageBox.warning(self, APP_NAME, "Save the masked copy under a different name "
                                                    "so your original is kept.")
                return
            jobs = [(src, out)]
            if out.exists() and QMessageBox.question(
                    self, APP_NAME, f"{out.name} already exists in that folder. Replace it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        else:
            out_dir, src_dir = Path(self.dir_out), Path(self.dir_src)
            if out_dir.resolve() == src_dir.resolve():
                QMessageBox.warning(self, APP_NAME, "Save the masked copies to a different folder "
                                                    "so your originals are kept.")
                return
            jobs = [(f, out_dir / f.name) for f in self.files]
            existing = [j for j in jobs if j[1].exists()]
            if existing and QMessageBox.question(
                    self, APP_NAME, f"{_plural(len(existing), 'file')} already exist in the output folder. "
                                    "Replace them?") != QMessageBox.Yes:
                return

        # layouts must make sense for the file on screen
        self._resolve()
        if self._problem:
            QMessageBox.warning(self, APP_NAME, "The layouts can't be applied yet.\n\n" + self._problem)
            return
        left = [p for p in range(1, self.page_count + 1)
                if p not in self._owner or not self.layouts[self._owner[p]].areas]
        if left:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning); box.setWindowTitle(APP_NAME)
            box.setText(f"{_plural(len(left), 'page')} will not be masked: {format_page_ranges(left)}.")
            box.setInformativeText("These pages have no layout with areas, so they will be copied "
                                   "unchanged and anything on them stays visible."
                                   + (" This check uses the file on screen." if not single else ""))
            go = box.addButton("Mask anyway", QMessageBox.AcceptRole)
            back = box.addButton("Go back", QMessageBox.RejectRole)
            box.setDefaultButton(back)
            box.exec()
            if box.clickedButton() is not go:
                return

        opts = Options(fill=FILLS[self.fill_combo.currentText()],
                       skip_other_orientation=self.skip_check.isChecked(),
                       reference_landscape=self.ref_landscape,  # only for layouts that recorded none
                       strict_lines=self.strict_check.isChecked())
        layouts = [Layout(l.name, l.ranges, list(l.areas), l.reference_landscape) for l in self.layouts]
        self.results, self.last_error = [], ""
        self.worker = MaskWorker(jobs, layouts, opts)
        self.worker.progress.connect(self._on_progress)
        self.worker.fileDone.connect(self._on_file_done)
        self.worker.fileResult.connect(self.results.append)
        self.worker.allDone.connect(self._on_all_done)
        self.log.clear()
        if not single:
            self.log.show()
        self.progress.setRange(0, self.page_count if single else len(jobs)); self.progress.setValue(0)
        self.progress.show()
        self.run_btn.setText("Cancel")
        self._set_editing(False)
        self.worker.start()

    def _on_progress(self, done: int, total: int):
        self.progress.setValue(done)
        self.status(f"Masking page {done} of {total}" if self.mode == "file" else f"Masking {done} of {total}")

    def _on_file_done(self, name: str, ok: bool, msg: str):
        if not ok:
            self.last_error = msg
        self.log.appendPlainText(f"{'Done' if ok else 'Failed'}   {name}   {msg}")
        if not ok:
            self.log.show()

    def _on_all_done(self, ok: int, failed: int, cancelled: bool):
        self._set_editing(True)
        self.run_btn.setEnabled(True)
        self.progress.hide()
        self._refresh()
        if self.mode == "file":
            self._finish_single(ok, failed, cancelled)
        else:
            self._finish_folder(ok, failed, cancelled)

    def _finish_single(self, ok: int, failed: int, cancelled: bool):
        box = QMessageBox(self); box.setWindowTitle(APP_NAME)
        if ok and self.results:
            r = self.results[0]
            head = f"Masked {r.pages_masked} of {r.page_count} pages."
            self.status(f"Saved {r.output.name}. " + head)
            box.setText(head)
            box.setInformativeText("\n".join(
                r.summary_lines() + ["", f"Saved as {r.output.name}.", "",
                                     "Spot-check the copy: search or select text inside a masked area "
                                     "to confirm nothing is left underneath."]))
            open_btn = box.addButton("Open output file", QMessageBox.AcceptRole)
            show_btn = box.addButton("Show in folder", QMessageBox.ActionRole)
            box.addButton("Close", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(r.output)))
            elif box.clickedButton() == show_btn:
                self._show_in_folder(r.output)
            return
        if cancelled:
            self.status("Stopped. Nothing was saved.")
            box.setText("Stopped. Nothing was saved.")
        else:
            self.status("Masking failed.")
            box.setText("Masking failed.")
            box.setInformativeText(self.last_error)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()

    def _finish_folder(self, ok: int, failed: int, cancelled: bool):
        out = self.dir_out
        summary = f"{_plural(ok, 'drawing')} masked" + (f", {failed} failed" if failed else "")
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

    @staticmethod
    def _show_in_folder(path: Path):
        if sys.platform == "win32":
            QProcess.startDetached("explorer.exe", ["/select,", QDir.toNativeSeparators(str(path))])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _set_editing(self, enabled: bool):
        for wdg in (self.step_src, self.step_out, self.step_areas, self.step_run, self.file_list,
                    self.view, self.strip, self.mode_buttons["file"], self.mode_buttons["folder"]):
            wdg.setEnabled(enabled)

    # ============================================================== helpers
    def _caption_for(self, page_index: int) -> str:
        page = page_index + 1
        idx = self._owner.get(page)
        if idx is None:
            return f"Page {page}: no layout, left unchanged. Draw an area to start one."
        lay = self.layouts[idx]
        extra = "" if lay.areas else ", no areas yet"
        shared = " (also claimed by another layout)" if page in self._clash else ""
        return f"Page {page}: {lay.name}, {_plural(len(lay.areas), 'area')}{extra}{shared}"

    def _on_strip_hover(self, index: int):
        self.strip_caption.setText(self._caption_for(index if index >= 0 else self.page_index)
                                   if self.page_count else "")

    def _refresh(self):
        has_page = bool(self.page_count)
        idx = self._page_layout() if has_page else None

        # areas on this page
        self.area_list.blockSignals(True)
        self.area_list.clear()
        areas = self.layouts[idx].areas if idx is not None else []
        for i, a in enumerate(areas, 1):
            self.area_list.addItem(
                f"Area {i}    {a.x0*100:.0f}–{a.x1*100:.0f}% across, {a.y0*100:.0f}–{a.y1*100:.0f}% down")
        self.area_list.blockSignals(False)
        if not areas:
            it = QListWidgetItem("No areas on this page"); it.setFlags(Qt.NoItemFlags)
            self.area_list.addItem(it)
        self.areas_title.setText(f"Areas on this page ({self.layouts[idx].name})" if idx is not None
                                 else "Areas on this page")

        # layout messages
        msgs = [m for m in (self._problem, self._row_error) if m]
        self.layout_msg.setText(" ".join(dict.fromkeys(msgs)))
        self.layout_msg.setVisible(bool(msgs))

        # strip
        masked = {i for i, lay in enumerate(self.layouts) if lay.areas}
        self.strip.set_state(self.page_count, self.page_index, self._owner, masked, self._clash)
        self.strip.setVisible(has_page)
        self.strip_bar.setVisible(has_page)
        self.strip_caption.setText(self._caption_for(self.page_index) if has_page else "")

        # sidebar steps
        single = self.mode == "file"
        has_src = bool(self.file_src) if single else bool(self.files)
        has_out = bool(self.out_edit.text())
        has_areas = self._has_areas()
        self.step_src.set_done(has_src)
        self.step_out.set_done(has_out)
        self.step_areas.set_done(has_areas and not self._problem)
        self.btn_remove_area.setEnabled(bool(areas))
        self.btn_clear_areas.setEnabled(bool(areas))
        self.btn_save_preset.setEnabled(has_areas)
        for b in (self.btn_add_layout, self.btn_split):
            b.setEnabled(has_page)
        self.btn_duplicate.setEnabled(has_page and self._selected_layout_index() is not None)
        self.btn_remove_layout.setEnabled(has_page and self._selected_layout_index() is not None)
        self.btn_split.setEnabled(has_page and idx is not None)

        running = bool(self.worker and self.worker.isRunning())
        self.files_header.setText(f"Drawings in folder ({len(self.files)})" if self.files else "Drawings in folder")
        if not running:
            if single:
                self.run_btn.setText("Mask this drawing")
            else:
                n = len(self.files)
                self.run_btn.setText(f"Mask {_plural(n, 'drawing')}" if n else "Mask drawings")
            self.run_btn.setEnabled(has_src and has_out and has_areas)
            if has_src and not has_areas:
                self.run_btn.setToolTip("Mark at least one area on the drawing first.")
            else:
                self.run_btn.setToolTip("Ctrl+Enter")

        self.page_label.setText(f"Page {self.page_index + 1} of {self.page_count}" if has_page else "Page –")
        self.prev_page.setEnabled(has_page and self.page_index > 0)
        self.next_page.setEnabled(has_page and self.page_index < self.page_count - 1)
        row = self.file_list.currentRow()
        self.prev_file.setEnabled(row > 0)
        self.next_file.setEnabled(0 <= row < self.file_list.count() - 1)

    def status(self, text: str):
        self.status_msg.setText(text)

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {APP_VERSION}<br>by {APP_AUTHOR}<br><br>"
                          "Permanently removes text, lines and images inside the areas you mark. "
                          "Mask one multi-page PDF into one masked PDF, or a whole folder of PDFs. "
                          "Layouts let different page ranges use different areas, for drawings whose "
                          "title block moves part way through. Everything runs on this computer; "
                          "no files are uploaded.<br><br>Built with PySide6 and PyMuPDF. "
                          "Typeface: IBM Plex Sans (SIL Open Font License).")

    def shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts",
                                "Ctrl+O    Open drawing file (or folder)\n"
                                "Ctrl+S    Save masked copy as\n"
                                "Ctrl+1 / Ctrl+2    One file / Folder\n"
                                "Ctrl+Z    Remove the last area\n"
                                "Delete    Remove the selected area\n"
                                "Ctrl+L    Start a new layout at this page\n"
                                "Ctrl+scroll    Zoom\n"
                                "Ctrl+0    Fit page\n"
                                "Middle-drag or Alt+drag    Pan\n"
                                "Page Up / Page Down    Change page\n"
                                "Ctrl+Enter    Mask")

    # ============================================================ drag/drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not (self.worker and self.worker.isRunning()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = Path(e.mimeData().urls()[0].toLocalFile())
        if p.is_dir():
            self.set_source_folder(p)
        elif p.suffix.lower() == ".pdf":
            self.set_source_file(p)
        else:
            self.status("Drop a PDF file or a folder.")

    # ============================================================= settings
    def _restore(self):
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

        # the app always starts empty: drop session state saved by older versions
        for key in self.settings.allKeys():
            if key not in KEPT_KEYS:
                self.settings.remove(key)
        self.mode = self.settings.value("mode", "file")
        if self.mode not in ("file", "folder"):
            self.mode = "file"

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME, "Masking is still running. Stop and quit?") != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel(); self.worker.wait(5000)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("mode", self.mode)
        super().closeEvent(e)
