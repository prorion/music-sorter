"""Compact task entries and shared in-dialog guidance."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout


class LiveStatus(QLabel):
    """One current message, elided without growing the dialog or accumulating logs."""
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.message = ''
        self.setObjectName('taskGuide')
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self.message = ' '.join(str(text).split())
        self.setToolTip(self.message)
        self.setFixedHeight(self.fontMetrics().height() + 24)
        self.render_message()

    def render_message(self):
        super().setText(self.fontMetrics().elidedText(self.message, Qt.TextElideMode.ElideRight, max(0, self.width() - 24)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_message()


class WorkflowButton(QPushButton):
    def __init__(self, number, title, hint, parent=None):
        super().__init__(parent)
        self.setObjectName('workflowButton')
        self.compact = False
        self.setAccessibleName(f'{number}. {title} · {hint}')
        self.setToolTip(hint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        self.caption = QLabel(f'{number}. {title}')
        self.caption.setObjectName('workflowCaption')
        self.hint = QLabel(hint)
        self.hint.setObjectName('subtle')
        for label in (self.caption, self.hint):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        self.setMinimumHeight(64)

    def sizeHint(self):
        # QPushButton's native hint only measures its own text, not child labels.
        return self.layout().sizeHint().expandedTo(QSize(0, 40 if self.compact else 56))

    def minimumSizeHint(self):
        return self.layout().minimumSize().expandedTo(QSize(0, 40 if self.compact else 56))

    def set_compact(self, compact):
        self.compact = compact
        self.hint.setVisible(not compact)
        self.setMinimumHeight(40 if compact else 64)
        self.updateGeometry()


def task_guide(layout, text):
    label = QLabel(text)
    label.setObjectName('taskGuide')
    label.setWordWrap(True)
    layout.addWidget(label)
    return label


def polish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
