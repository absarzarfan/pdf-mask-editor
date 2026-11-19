"""Page preview where the user draws, moves and resizes mask areas."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView)

from .core import Area
from .theme import FONT, REDLINE, TABLE

HANDLE = 9  # px on screen
MIN_SIZE = 6


class MaskItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, bounds: QRectF, index: int, on_change):
        super().__init__(rect)
        self.bounds = bounds
        self.index = index
        self.on_change = on_change
        self._resizing = False
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemIsFocusable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)
        self.setZValue(10)

    # -- geometry helpers
    def scene_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def _handle_size(self) -> float:
        views = self.scene().views() if self.scene() else []
        scale = views[0].transform().m11() if views else 1.0
        return HANDLE / max(scale, 1e-6)

    def _handle_rect(self) -> QRectF:
        s = self._handle_size()
        br = self.rect().bottomRight()
        return QRectF(br.x() - s, br.y() - s, s * 2, s * 2)

    # -- painting
    def boundingRect(self) -> QRectF:
        s = self._handle_size()
        return self.rect().adjusted(-2, -2, s + 2, s + 2)

    def paint(self, p: QPainter, option, widget=None):
        r = self.rect()
        red = QColor(REDLINE)
        fill = QColor(red); fill.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRect(r)
        hatch = QBrush(red, Qt.BDiagPattern)
        p.setBrush(hatch)
        p.drawRect(r)
        pen = QPen(red, 2.4 if self.isSelected() else 1.6)
        pen.setCosmetic(True)
        if not self.isSelected():
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        # area number tag, constant size on screen
        s = self._handle_size()
        tag = QRectF(r.left(), r.top(), s * 2.4, s * 2.0)
        p.setPen(Qt.NoPen)
        p.setBrush(red)
        p.drawRect(tag)
        p.setPen(QColor("white"))
        f = QFont(FONT); f.setPixelSize(max(1, int(s * 1.35))); f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(tag, Qt.AlignCenter, str(self.index))

        if self.isSelected():
            p.setPen(Qt.NoPen)
            p.setBrush(red)
            p.drawRect(self._handle_rect().adjusted(s * 0.5, s * 0.5, -s * 0.5, -s * 0.5))

    # -- interaction
    def hoverMoveEvent(self, e):
        self.setCursor(Qt.SizeFDiagCursor if self._handle_rect().contains(e.pos()) else Qt.SizeAllCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._handle_rect().contains(e.pos()):
            self._resizing = True
            self.setSelected(True)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            r = QRectF(self.rect())
            br_scene = self.mapToScene(e.pos())
            br_scene.setX(min(br_scene.x(), self.bounds.right()))
            br_scene.setY(min(br_scene.y(), self.bounds.bottom()))
            br = self.mapFromScene(br_scene)
            r.setRight(max(r.left() + MIN_SIZE, br.x()))
            r.setBottom(max(r.top() + MIN_SIZE, br.y()))
            self.prepareGeometryChange()
            self.setRect(r)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        changed = self._resizing or e.buttonDownScenePos(Qt.LeftButton) != e.scenePos()
        self._resizing = False
        super().mouseReleaseEvent(e)
        if changed:
            self.on_change()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            r = self.rect().translated(value)
            dx = dy = 0.0
            if r.left() < self.bounds.left(): dx = self.bounds.left() - r.left()
            if r.right() > self.bounds.right(): dx = self.bounds.right() - r.right()
            if r.top() < self.bounds.top(): dy = self.bounds.top() - r.top()
            if r.bottom() > self.bounds.bottom(): dy = self.bounds.bottom() - r.bottom()
            return QPointF(value.x() + dx, value.y() + dy)
        return super().itemChange(change, value)


class PreviewView(QGraphicsView):
    areasChanged = Signal()
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Table")
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(TABLE))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)

        self.page_item: QGraphicsPixmapItem | None = None
        self.items_: list[MaskItem] = []
        self._origin: QPointF | None = None
        self._ghost: QGraphicsRectItem | None = None
        self._fit = True
        self._panning = False
        self._pan_start = None
        self.empty_text = "Choose a drawing file to preview it"

    # ----------------------------------------------------------- page / areas
    def bounds(self) -> QRectF:
        return self.page_item.sceneBoundingRect() if self.page_item else QRectF()

    def set_page(self, image: QImage | None, areas: list[Area]):
        self.scene().clear()
        self.items_.clear()
        self.page_item = None
        if image is None or image.isNull():
            self.scene().setSceneRect(QRectF())
            self.viewport().update()
            return
        self.page_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.page_item.setTransformationMode(Qt.SmoothTransformation)
        b = self.bounds()
        shadow = self.scene().addRect(b.translated(6, 6), QPen(Qt.NoPen), QColor(0, 0, 0, 60))
        shadow.setZValue(-1)
        self.scene().setSceneRect(b.adjusted(-40, -40, 40, 40))
        self.set_areas(areas)
        if self._fit:
            self.fit()

    def set_areas(self, areas: list[Area]):
        """Replace the areas shown on the current page. Does not emit areasChanged."""
        for it in self.items_:
            self.scene().removeItem(it)
        self.items_.clear()
        b = self.bounds()
        if b.isEmpty():
            return
        for a in areas:
            self._add_item(QRectF(b.left() + a.x0 * b.width(), b.top() + a.y0 * b.height(),
                                  (a.x1 - a.x0) * b.width(), (a.y1 - a.y0) * b.height()))

    def areas(self) -> list[Area]:
        b = self.bounds()
        if b.isEmpty():
            return []
        out = []
        for it in self.items_:
            r = it.scene_rect()
            out.append(Area((r.left() - b.left()) / b.width(), (r.top() - b.top()) / b.height(),
                            (r.right() - b.left()) / b.width(), (r.bottom() - b.top()) / b.height()).normalized())
        return out

    def _add_item(self, rect: QRectF):
        item = MaskItem(rect, self.bounds(), len(self.items_) + 1, self.areasChanged.emit)
        self.scene().addItem(item)
        self.items_.append(item)

    def _renumber(self):
        for i, it in enumerate(self.items_, 1):
            it.index = i
            it.update()

    def remove_selected(self) -> bool:
        sel = [i for i in self.items_ if i.isSelected()]
        for it in sel:
            self.scene().removeItem(it)
            self.items_.remove(it)
        if sel:
            self._renumber()
            self.areasChanged.emit()
        return bool(sel)

    def remove_last(self):
        if self.items_:
            self.scene().removeItem(self.items_.pop())
            self.areasChanged.emit()

    def remove_index(self, idx: int):
        if 0 <= idx < len(self.items_):
            self.scene().removeItem(self.items_.pop(idx))
            self._renumber()
            self.areasChanged.emit()

    def select_index(self, idx: int):
        for i, it in enumerate(self.items_):
            it.setSelected(i == idx)

    def clear_areas(self):
        for it in self.items_:
            self.scene().removeItem(it)
        self.items_.clear()
        self.areasChanged.emit()

    # ----------------------------------------------------------------- zoom
    def fit(self):
        if self.page_item:
            self._fit = True
            self.fitInView(self.bounds().adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
            self.zoomChanged.emit(self.transform().m11())

    def zoom_by(self, factor: float):
        new = self.transform().m11() * factor
        if 0.05 < new < 12:
            self._fit = False
            self.scale(factor, factor)
            self.zoomChanged.emit(new)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit:
            self.fit()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(e)

    # -------------------------------------------------------------- drawing
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self._panning, self._pan_start = True, e.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        item = self.itemAt(e.position().toPoint())
        if e.button() == Qt.LeftButton and self.page_item and not isinstance(item, MaskItem):
            pt = self.mapToScene(e.position().toPoint())
            if self.bounds().contains(pt):
                self.scene().clearSelection()
                self._origin = pt
                pen = QPen(QColor(REDLINE), 1.5, Qt.DashLine); pen.setCosmetic(True)
                fill = QColor(REDLINE); fill.setAlpha(30)
                self._ghost = self.scene().addRect(QRectF(pt, pt), pen, fill)
                self._ghost.setZValue(20)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._ghost:
            b = self.bounds()
            pt = self.mapToScene(e.position().toPoint())
            pt = QPointF(min(max(pt.x(), b.left()), b.right()), min(max(pt.y(), b.top()), b.bottom()))
            self._ghost.setRect(QRectF(self._origin, pt).normalized())
            return
        if self.page_item and not isinstance(self.itemAt(e.position().toPoint()), MaskItem):
            inside = self.bounds().contains(self.mapToScene(e.position().toPoint()))
            self.viewport().setCursor(Qt.CrossCursor if inside else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            return
        if self._ghost:
            r = self._ghost.rect()
            self.scene().removeItem(self._ghost)
            self._ghost = None
            scale = self.transform().m11()
            if r.width() * scale > MIN_SIZE and r.height() * scale > MIN_SIZE:
                self._add_item(r)
                self.items_[-1].setSelected(True)
                self.areasChanged.emit()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.remove_selected():
                return
        if e.key() == Qt.Key_Escape:
            self.scene().clearSelection()
            return
        super().keyPressEvent(e)

    # ---------------------------------------------------------- empty state
    def drawForeground(self, p: QPainter, rect: QRectF):
        if self.page_item is None:
            p.save()
            p.resetTransform()
            p.setPen(QColor(255, 255, 255, 170))
            f = QFont(FONT, 11)
            p.setFont(f)
            p.drawText(self.viewport().rect(), Qt.AlignCenter, self.empty_text)
            p.restore()
