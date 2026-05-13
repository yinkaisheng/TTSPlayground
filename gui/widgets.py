# -- coding: utf-8 --
"""跨 Tab 复用的轻量控件。"""

from PyQt5.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPolygon
from PyQt5.QtWidgets import (
    QComboBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QTextEdit,
)

from gui.constants import UI_ARROW_GLYPH_BASE_PX, UI_ARROW_GLYPH_HEIGHT_PX


def _paint_triangle_centroid_at(
    painter: QPainter,
    cx: float,
    cy: float,
    base_w: int,
    height: int,
    *,
    up: bool,
) -> None:
    """等腰三角，外接矩形在竖直方向以 ``(cx, cy)`` 为中心（非几何重心，避免朝下三角视觉下移）。"""
    if base_w < 4 or height < 4:
        return
    hb = base_w / 2.0
    half = height / 2.0
    if up:
        y_top = cy - half
        y_bot = cy + half
        poly = QPolygon(
            [
                QPoint(int(round(cx)), int(round(y_top))),
                QPoint(int(round(cx - hb)), int(round(y_bot))),
                QPoint(int(round(cx + hb)), int(round(y_bot))),
            ]
        )
    else:
        y_top = cy - half
        y_bot = cy + half
        poly = QPolygon(
            [
                QPoint(int(round(cx - hb)), int(round(y_top))),
                QPoint(int(round(cx + hb)), int(round(y_top))),
                QPoint(int(round(cx)), int(round(y_bot))),
            ]
        )
    painter.drawPolygon(poly)


class SingleLineElidingInfoLabel(QLabel):
    """单行展示 YAML ``info``；过长省略号，仅当被裁切时悬停显示全文 tooltip。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("cardSectionModelInfo")
        self._full = ""
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setMinimumWidth(40)

    def set_full_text(self, text: str) -> None:
        self._full = (text or "").strip()
        self._apply_elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_elide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._apply_elide()

    def _apply_elide(self) -> None:
        full = self._full
        if not full:
            self.clear()
            self.setToolTip("")
            return
        margin = 8
        avail = max(0, int(self.width()) - margin)
        fm = QFontMetrics(self.font())
        if avail <= 0:
            self.setText(full)
            self.setToolTip("")
            return
        elided = fm.elidedText(full, Qt.ElideRight, avail)
        self.setText(elided)
        self.setToolTip(full if elided != full else "")


class WsLogTextEdit(QTextEdit):
    """WebSocket / Realtime 日志：压低 Qt 默认 QTextEdit 的 size 提示，避免整窗最小高度超出初始 resize。"""

    def minimumSizeHint(self):
        fm = self.fontMetrics()
        h = fm.lineSpacing() * 2 + 10
        return QSize(80, max(28, min(int(h), 52)))

    def sizeHint(self):
        fm = self.fontMetrics()
        h = fm.lineSpacing() * 3 + 12
        return QSize(280, max(36, min(int(h), 72)))


class GtArrowComboBox(QComboBox):
    """在系统绘制完成后于下拉区补画三角；三角大小见 ``constants.UI_ARROW_GLYPH_*``，仅位置随控件居中。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        st = self.style()
        arrow = st.subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxArrow, self)
        if arrow.isNull() or arrow.width() < 2:
            arrow = st.subControlRect(
                QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxDropDown, self
            )
        if arrow.isNull() or arrow.width() < 2:
            return
        c = QRectF(arrow).center()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#1a1c1e"))
            _paint_triangle_centroid_at(
                painter,
                c.x(),
                c.y(),
                UI_ARROW_GLYPH_BASE_PX,
                UI_ARROW_GLYPH_HEIGHT_PX,
                up=False,
            )
        finally:
            painter.end()


class GlyphSpinBox(QSpinBox):
    """部分环境下 QSS 无法绘制箭头；在框架绘制后补画三角（大小固定为 ``UI_ARROW_GLYPH_*``）。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        st = self.style()
        up_r = st.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxUp, self)
        dn_r = st.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxDown, self)
        if up_r.isNull() or dn_r.isNull():
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#1a1c1e"))
            cu = QRectF(up_r).center()
            cd = QRectF(dn_r).center()
            _paint_triangle_centroid_at(
                painter,
                cu.x(),
                cu.y(),
                UI_ARROW_GLYPH_BASE_PX,
                UI_ARROW_GLYPH_HEIGHT_PX,
                up=True,
            )
            _paint_triangle_centroid_at(
                painter,
                cd.x(),
                cd.y(),
                UI_ARROW_GLYPH_BASE_PX,
                UI_ARROW_GLYPH_HEIGHT_PX,
                up=False,
            )
        finally:
            painter.end()
