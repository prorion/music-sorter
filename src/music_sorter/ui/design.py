"""Code-native visual components without external assets."""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout


def icon(name, color="#8796AB", size=22):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name in {"music", "library"}:
        painter.drawLine(10, 17, 10, 5)
        painter.drawLine(10, 5, 20, 3)
        painter.drawLine(20, 3, 20, 15)
        painter.drawEllipse(QRectF(4, 15, 6, 5))
        painter.drawEllipse(QRectF(14, 13, 6, 5))
    elif name == "review":
        painter.drawRoundedRect(QRectF(5, 3, 14, 18), 3, 3)
        painter.drawLine(9, 8, 15, 8)
        painter.drawLine(8, 14, 11, 17)
        painter.drawLine(11, 17, 16, 12)
    elif name == "duplicates":
        painter.drawRoundedRect(QRectF(3, 3, 13, 13), 3, 3)
        painter.drawRoundedRect(QRectF(8, 8, 13, 13), 3, 3)
    elif name == "playlist":
        for y, end in ((5, 19), (10, 19), (15, 13)):
            painter.drawLine(4, y, end, y)
        painter.drawLine(17, 14, 17, 20)
        painter.drawLine(14, 17, 20, 17)
    elif name == "history":
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawLine(12, 7, 12, 12)
        painter.drawLine(12, 12, 16, 14)
    elif name == "settings":
        painter.drawEllipse(QRectF(5, 5, 14, 14))
        painter.drawEllipse(QRectF(9, 9, 6, 6))
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(12, 12)
            painter.rotate(angle)
            painter.drawLine(0, -7, 0, -10)
            painter.restore()
    elif name == "play":
        painter.setBrush(QColor(color))
        path = QPainterPath()
        path.moveTo(8, 5)
        path.lineTo(19, 12)
        path.lineTo(8, 19)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "pause":
        painter.drawLine(8, 5, 8, 19)
        painter.drawLine(16, 5, 16, 19)
    elif name == "search":
        painter.drawEllipse(QRectF(3, 3, 13, 13))
        painter.drawLine(15, 15, 21, 21)
    elif name == "detail":
        painter.drawRoundedRect(QRectF(3, 4, 18, 16), 3, 3)
        painter.drawLine(15, 4, 15, 20)
    else:
        painter.drawRoundedRect(QRectF(3, 5, 18, 14), 3, 3)
        painter.drawLine(3, 9, 21, 9)
    painter.end()
    return QIcon(pixmap)


class StatCard(QFrame):
    def __init__(self, label, hint, tint="mint", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setProperty("tint", tint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("muted")
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        subtitle = QLabel(hint)
        subtitle.setObjectName("subtle")
        layout.addWidget(title)
        layout.addWidget(self.value)
        layout.addWidget(subtitle)


class TrackDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() not in {5, 6}:
            return super().paint(painter, option, index)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text, styled.text = styled.text, ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        dark = bool(QApplication.instance().property("musicDark"))
        good, neutral = text in {"확정", "확인됨"}, text == "미분류"
        if good:
            bg, fg = ("#193D34", "#8DE4C0") if dark else ("#E3F5ED", "#226D55")
        elif neutral:
            bg, fg = ("#242E40", "#A4B2C8") if dark else ("#EDF0F6", "#60718B")
        else:
            bg, fg = ("#3B3021", "#EAC48A") if dark else ("#FBF0DB", "#926619")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = option.font
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        painter.setFont(font)
        width = min(option.rect.width() - 12, painter.fontMetrics().horizontalAdvance(text) + 20)
        rectangle = QRectF(option.rect.left() + 6, option.rect.center().y() - 12, width, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rectangle, 7, 7)
        painter.setPen(QColor(fg))
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(48, size.height() + 16))
