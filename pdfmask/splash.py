"""Splash screen: a small drawing sheet with a title block, one row of it masked."""
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import APP_AUTHOR, APP_CREDIT, APP_NAME, APP_VERSION
from .theme import FONT, FONT_CONDENSED, INK, MUTED, REDLINE, RULE

W, H = 760, 440


class Splash(QWidget):
    finished = Signal()

    def __init__(self, duration_ms: int = 1900):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self._progress = 0.0
        self._status = "Loading drawing tools"
        self._step = 20 / duration_ms
        self._timer = QTimer(self, interval=20, timeout=self._tick)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - W // 2, screen.center().y() - H // 2)

    def start(self):
        self.show()
        self._timer.start()

    def _tick(self):
        self._progress = min(1.0, self._progress + self._step)
        if self._progress > 0.45:
            self._status = "Preparing the redaction engine"
        if self._progress > 0.8:
            self._status = "Ready"
        self.update()
        if self._progress >= 1.0:
            self._timer.stop()
            QTimer.singleShot(180, self._done)

    def _done(self):
        self.close()
        self.finished.emit()

    # ------------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        ink, muted, rule, red = QColor(INK), QColor(MUTED), QColor(RULE), QColor(REDLINE)

        # sheet + soft edge
        sheet = QRectF(8, 8, W - 16, H - 16)
        for i, a in enumerate((10, 16, 24)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 30, 40, a))
            p.drawRoundedRect(sheet.adjusted(-6 + i * 2, -4 + i * 2, 6 - i * 2, 8 - i * 2), 6, 6)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRect(sheet)

        # diagonal credit watermark
        p.save()
        p.setClipRect(sheet)
        p.setPen(QColor(31, 43, 56, 13))
        p.setFont(QFont(FONT, 15, QFont.DemiBold))
        p.translate(W / 2, H / 2)
        p.rotate(-24)
        for row in range(-6, 7):
            for col in range(-3, 4):
                p.drawText(col * 300 + (row % 2) * 150 - 120, row * 62, APP_AUTHOR)
        p.restore()

        # drawing frame with zone markers
        frame = sheet.adjusted(22, 22, -22, -22)
        p.setPen(QPen(ink, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRect(frame)
        p.setPen(QPen(ink, 0.6))
        p.drawRect(sheet.adjusted(10, 10, -10, -10))

        p.setFont(QFont(FONT, 7))
        p.setPen(muted)
        cols, rows = 6, 4
        for i in range(cols):
            x = frame.left() + frame.width() * (i + 0.5) / cols
            for y in (sheet.top() + 21, frame.bottom() + 12):
                p.drawText(QRectF(x - 10, y - 9, 20, 12), Qt.AlignCenter, str(i + 1))
            if i:
                xx = frame.left() + frame.width() * i / cols
                p.drawLine(int(xx), int(frame.top()) - 6, int(xx), int(frame.top()))
                p.drawLine(int(xx), int(frame.bottom()), int(xx), int(frame.bottom()) + 6)
        for j in range(rows):
            y = frame.top() + frame.height() * (j + 0.5) / rows
            for x in (sheet.left() + 16, frame.right() + 11):
                p.drawText(QRectF(x - 8, y - 6, 16, 12), Qt.AlignCenter, "ABCD"[j])

        # title
        p.setPen(ink)
        p.setFont(QFont(FONT_CONDENSED, 40, QFont.Bold))
        p.drawText(QRectF(frame.left() + 34, frame.top() + 34, 500, 64), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
        p.setPen(muted)
        p.setFont(QFont(FONT, 11))
        p.drawText(QRectF(frame.left() + 36, frame.top() + 96, 500, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   "Mask the same area on every page of every drawing, in one pass.")

        # a dimension line under the title, drafting style
        y = frame.top() + 140
        x0, x1 = frame.left() + 36, frame.left() + 300
        p.setPen(QPen(muted, 0.8))
        p.drawLine(int(x0), int(y), int(x1), int(y))
        for x in (x0, x1):
            p.drawLine(int(x), int(y - 5), int(x), int(y + 5))
        p.setFont(QFont(FONT, 8))
        p.drawText(QRectF(x0, y - 18, x1 - x0, 14), Qt.AlignCenter, f"v{APP_VERSION}")

        # title block, bottom-right
        tb = QRectF(frame.right() - 330, frame.bottom() - 132, 330, 132)
        row_h = tb.height() / 4
        label_w = 92
        rows_data = [
            ("Title", APP_NAME),
            ("Client", "Confidential client name"),
            ("Drawn by", APP_AUTHOR),
            ("Rev", f"{APP_VERSION}    Sheet 1 of 1"),
        ]
        p.setPen(QPen(ink, 1.2))
        p.drawRect(tb)
        for i, (label, value) in enumerate(rows_data):
            r = QRectF(tb.left(), tb.top() + i * row_h, tb.width(), row_h)
            if i:
                p.setPen(QPen(ink, 0.7))
                p.drawLine(r.topLeft(), r.topRight())
            p.setPen(QPen(ink, 0.7))
            p.drawLine(int(r.left() + label_w), int(r.top()), int(r.left() + label_w), int(r.bottom()))
            p.setPen(muted)
            p.setFont(QFont(FONT, 8))
            p.drawText(r.adjusted(8, 0, 0, 0).intersected(QRectF(r.left() + 8, r.top(), label_w - 8, row_h)),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(ink)
            p.setFont(QFont(FONT, 10, QFont.DemiBold if i in (0, 2) else QFont.Normal))
            p.drawText(QRectF(r.left() + label_w + 10, r.top(), r.width() - label_w - 14, row_h),
                       Qt.AlignLeft | Qt.AlignVCenter, value)

        # the masked row: redline hatch over the client value
        mask = QRectF(tb.left() + label_w + 4, tb.top() + row_h + 4, tb.width() - label_w - 8, row_h - 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRect(mask)
        red_fill = QColor(red); red_fill.setAlpha(28)
        p.setBrush(red_fill)
        p.drawRect(mask)
        p.setBrush(QBrush(red, Qt.BDiagPattern))
        p.drawRect(mask)
        p.setPen(QPen(red, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(mask)

        # credit + status, bottom-left
        left = frame.left() + 36
        p.setPen(ink)
        p.setFont(QFont(FONT, 10, QFont.DemiBold))
        p.drawText(QRectF(left, frame.bottom() - 74, 320, 20), Qt.AlignLeft | Qt.AlignVCenter, APP_CREDIT)

        p.setPen(muted)
        p.setFont(QFont(FONT, 9))
        p.drawText(QRectF(left, frame.bottom() - 48, 320, 18), Qt.AlignLeft | Qt.AlignVCenter, self._status)

        bar = QRectF(left, frame.bottom() - 26, 260, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(rule)
        p.drawRect(bar)
        p.setBrush(ink)
        p.drawRect(QRectF(bar.left(), bar.top(), bar.width() * self._progress, bar.height()))
        p.end()
