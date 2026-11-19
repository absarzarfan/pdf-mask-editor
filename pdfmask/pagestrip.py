"""Compact strip with one cell per page, showing which layout each page belongs to."""
from __future__ import annotations

from math import ceil

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import FONT, INK, LAYOUT_TONES, MUTED, REDLINE, RULE, SHEET

MIN_CELL = 16
CELL_H = 26


class PageStrip(QWidget):
    """Neutral tones tell layouts apart. Redline appears only on pages that get masked.

    Pages without a layout are drawn white with a dashed outline and diagonal hatching.
    """
    pageClicked = Signal(int)   # 0-based
    pageHovered = Signal(int)   # 0-based, -1 when the pointer leaves

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._count = 0
        self._current = 0
        self._owner: dict[int, int] = {}     # 1-based page -> layout index
        self._masked: set[int] = set()       # layout indexes that have areas
        self._clash: set[int] = set()        # 1-based pages claimed by more than one layout
        self._hover = -1
        self._fit_height()

    def set_state(self, page_count: int, current: int, owner: dict[int, int],
                  masked_layouts: set[int], clash_pages: set[int]):
        self._count, self._current = page_count, current
        self._owner, self._masked, self._clash = owner, masked_layouts, clash_pages
        self._fit_height()
        self.update()

    # ---- geometry
    def _grid(self) -> tuple[int, float, int]:
        """columns, cell width, rows for the current width."""
        n = max(self._count, 1)
        cols = max(1, min(n, self.width() // MIN_CELL))
        return cols, self.width() / cols, ceil(n / cols)

    def _fit_height(self):
        rows = self._grid()[2] if self._count else 1
        self.setFixedHeight(rows * CELL_H + 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_height()

    def _cell_rect(self, i: int) -> QRectF:
        cols, w, _ = self._grid()
        return QRectF((i % cols) * w, (i // cols) * CELL_H, w, CELL_H - 2)

    def _index_at(self, x: float, y: float) -> int:
        if not self._count:
            return -1
        cols, w, _ = self._grid()
        col, row = int(x // w), int(y // CELL_H)
        i = row * cols + col
        return i if 0 <= col < cols and 0 <= i < self._count else -1

    # ---- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        f = QFont(FONT); f.setPixelSize(9); p.setFont(f)
        for i in range(self._count):
            page = i + 1
            r = self._cell_rect(i).adjusted(0.5, 0.5, -0.5, -0.5)
            idx = self._owner.get(page)
            if idx is None:
                p.fillRect(r, QColor(SHEET))
                p.fillRect(r, QBrush(QColor(RULE), Qt.BDiagPattern))
                pen = QPen(QColor(MUTED), 1, Qt.DashLine)
            else:
                p.fillRect(r, QColor(LAYOUT_TONES[idx % len(LAYOUT_TONES)]))
                pen = QPen(QColor(RULE), 1)
                if idx in self._masked:
                    p.fillRect(QRectF(r.left(), r.bottom() - 3, r.width(), 3), QColor(REDLINE))
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            # a heavier edge where the layout changes
            if idx is not None and i > 0 and self._owner.get(page - 1) != idx:
                p.setPen(QPen(QColor(INK), 2))
                p.drawLine(r.topLeft(), r.bottomLeft())
            p.setPen(QColor(INK) if idx is not None else QColor(MUTED))
            label = "!" if page in self._clash else str(page)
            if r.width() >= 13:
                p.drawText(r.adjusted(0, 0, 0, -3), Qt.AlignCenter, label)
            if i == self._hover and i != self._current:
                p.setPen(QPen(QColor(MUTED), 1)); p.setBrush(Qt.NoBrush); p.drawRect(r.adjusted(1, 1, -1, -1))
            if i == self._current:
                p.setPen(QPen(QColor(INK), 2)); p.setBrush(Qt.NoBrush); p.drawRect(r.adjusted(1, 1, -1, -1))
        p.end()

    # ---- input
    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x(), e.position().y())
        if i != self._hover:
            self._hover = i
            self.pageHovered.emit(i)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.pageHovered.emit(-1)
        self.update()

    def mousePressEvent(self, e):
        i = self._index_at(e.position().x(), e.position().y())
        if e.button() == Qt.LeftButton and i >= 0:
            self.pageClicked.emit(i)
