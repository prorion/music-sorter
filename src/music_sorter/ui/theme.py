from PySide6.QtGui import QColor, QPalette, QWheelEvent
from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QDialog, QDialogButtonBox, QLineEdit, QMessageBox, QPushButton
from pathlib import Path


class DialogButtonStyler(QObject):
    """Style standard dialog actions without changing keyboard defaults or callbacks."""
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Show and isinstance(widget, QDialog):
            if not isinstance(widget, QMessageBox):
                # Opening a task must not give Enter an implicit paid/destructive action.
                for button in widget.findChildren(QPushButton):
                    button.setAutoDefault(False)
            for box in widget.findChildren(QDialogButtonBox):
                for button in box.buttons():
                    role = box.buttonRole(button)
                    labels = {QDialogButtonBox.StandardButton.Ok: '확인', QDialogButtonBox.StandardButton.Cancel: '취소',
                              QDialogButtonBox.StandardButton.Apply: '적용', QDialogButtonBox.StandardButton.Close: '닫기',
                              QDialogButtonBox.StandardButton.Yes: '예', QDialogButtonBox.StandardButton.No: '아니요',
                              QDialogButtonBox.StandardButton.Save: '저장', QDialogButtonBox.StandardButton.Discard: '저장 안 함'}
                    if box.standardButton(button) in labels:
                        button.setText(labels[box.standardButton(button)])
                    button.setProperty('primary', role in {QDialogButtonBox.ButtonRole.AcceptRole, QDialogButtonBox.ButtonRole.YesRole})
                    button.setProperty('applyAction', role == QDialogButtonBox.ButtonRole.ApplyRole)
                    button.setProperty('danger', role == QDialogButtonBox.ButtonRole.DestructiveRole)
                    button.style().unpolish(button)
                    button.style().polish(button)
        return False


class ComboWheelGuard(QObject):
    """Closed dropdowns must never consume scrolling as a value change."""
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Wheel:
            combo = widget if isinstance(widget, QComboBox) else widget.parentWidget() if isinstance(widget, QLineEdit) else None
            if isinstance(combo, QComboBox) and not combo.view().isVisible():
                parent = combo.parentWidget()
                while parent:
                    if isinstance(parent, QAbstractScrollArea):
                        forwarded = QWheelEvent(parent.viewport().mapFromGlobal(event.globalPosition().toPoint()),
                                                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                                event.buttons(), event.modifiers(), event.phase(), event.inverted())
                        forwarded.ignore()
                        QApplication.sendEvent(parent.viewport(), forwarded)
                        if forwarded.isAccepted():
                            break
                    parent = parent.parentWidget()
                event.ignore()
                return True
        return False


def apply_theme(app, theme: str, font_scale: float):
    if not hasattr(app, '_combo_wheel_guard'):
        app._combo_wheel_guard = ComboWheelGuard(app)
        app.installEventFilter(app._combo_wheel_guard)
    if not hasattr(app, '_dialog_button_styler'):
        app._dialog_button_styler = DialogButtonStyler(app)
        app.installEventFilter(app._dialog_button_styler)
    dark = theme == 'dark' or (theme == 'system' and app.styleHints().colorScheme().name == 'Dark')
    app.setProperty('musicDark', dark)
    background, panel, raised, text, muted, border, accent, soft, selected = (
        '#0D131D', '#151E2B', '#1C2839', '#E7EEF7', '#92A3BA', '#273448', '#86DDBA', '#183A32', '#223447'
    ) if dark else ('#F3F5F9', '#FFFFFF', '#F8FAFD', '#1B2C43', '#6A7C94', '#E2E8F1', '#247F68', '#E3F3ED', '#EAF0F8')
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, background), (QPalette.ColorRole.Base, panel),
                        (QPalette.ColorRole.AlternateBase, raised), (QPalette.ColorRole.WindowText, text),
                        (QPalette.ColorRole.Text, text), (QPalette.ColorRole.ButtonText, text),
                        (QPalette.ColorRole.Button, panel), (QPalette.ColorRole.Highlight, selected),
                        (QPalette.ColorRole.HighlightedText, text), (QPalette.ColorRole.PlaceholderText, muted)):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = app.font()
    font.setFamily('맑은 고딕')
    font.setPointSizeF(10 * font_scale)
    app.setFont(font)
    app.setStyle('Fusion')
    check_image = (Path(__file__).parents[1] / 'resources' / 'check.svg').as_posix()
    arrow_root = Path(__file__).parents[1] / 'resources'
    up_image = (arrow_root / f'arrow-up-{"dark" if dark else "light"}.svg').as_posix()
    down_image = (arrow_root / f'arrow-down-{"dark" if dark else "light"}.svg').as_posix()
    disabled_up = (arrow_root / 'arrow-up-disabled.svg').as_posix()
    disabled_down = (arrow_root / 'arrow-down-disabled.svg').as_posix()
    app.setStyleSheet(f'''
        QWidget {{ color: {text}; }}
        QMainWindow, QDialog, QWidget#workspace {{ background: {background}; }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#muted, QLabel#subtle {{ color: {muted}; }}
        QLabel#subtle {{ font-size: 11px; }}
        QLabel#pageTitle {{ font-size: 25px; font-weight: 700; }}
        QLabel#brand {{ font-size: 19px; font-weight: 700; letter-spacing: 1px; }}
        QLabel#statValue {{ font-size: 21px; font-weight: 700; }}
        QFrame#statCard, QWidget#detailPanel, QFrame#playerBar, QWidget#sidebar {{ background: {panel}; border: 1px solid {border}; border-radius: 14px; }}
        QWidget#sidebar {{ border: none; }}
        QWidget#trackEditor {{ background: {panel}; }}
        QFrame#statCard[tint="mint"] QLabel#statValue {{ color: {accent}; }}
        QLabel#badge {{ color: {accent}; background: {soft}; border-radius: 7px; padding: 5px 10px; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px 11px; min-height: 21px; selection-background-color: {soft}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
        QLineEdit[storagePath="true"] {{ background: {raised}; }}
        QComboBox {{ padding-right: 34px; min-width: 140px; }}
        QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: top right; width: 28px; border: none; }}
        QComboBox::down-arrow {{ image: url("{down_image}"); width: 10px; height: 6px; }}
        QSpinBox, QDoubleSpinBox {{ padding-right: 36px; min-width: 140px; min-height: 28px; }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 28px; height: 22px; background: {raised}; border-left: 1px solid {border}; border-bottom: 1px solid {border}; border-top-right-radius: 7px; }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 28px; height: 22px; background: {raised}; border-left: 1px solid {border}; border-bottom-right-radius: 7px; }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {selected}; }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up_image}"); width: 10px; height: 6px; }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down_image}"); width: 10px; height: 6px; }}
        QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:off {{ image: url("{disabled_up}"); }}
        QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:off {{ image: url("{disabled_down}"); }}
        QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {border}; selection-background-color: {selected}; padding: 6px; }}
        QPushButton {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px 13px; min-height: 20px; }}
        QPushButton:hover {{ background: {raised}; border-color: {muted}; }}
        QPushButton:pressed {{ background: {selected}; }}
        QPushButton:focus {{ border: 1px solid {accent}; }}
        QPushButton:disabled {{ color: {muted}; border-color: {border}; background: {raised}; }}
        QPushButton[primary="true"] {{ background: {accent}; color: {'#10251E' if dark else '#FFFFFF'}; border: 1px solid {accent}; font-weight: 600; }}
        QPushButton[primary="true"]:hover {{ background: {'#A4EACD' if dark else '#1C6855'}; border-color: {'#A4EACD' if dark else '#1C6855'}; }}
        QPushButton[primary="true"]:pressed {{ background: {'#68CBA6' if dark else '#155242'}; }}
        QPushButton[applyAction="true"] {{ color: {accent}; border-color: {accent}; background: {soft}; }}
        QPushButton[danger="true"] {{ color: {'#F1A6A6' if dark else '#B42332'}; }}
        QDialogButtonBox QPushButton {{ min-width: 72px; }}
        QPushButton[primary="true"]:focus {{ border: 2px solid {'#E7EEF7' if dark else '#163D32'}; padding: 7px 12px; }}
        QPushButton[primary="true"]:disabled {{ background: {raised}; color: {muted}; border: 1px solid {border}; }}
        QPushButton[applyAction="true"]:disabled {{ background: {raised}; color: {muted}; border-color: {border}; }}
        QPushButton#ghost {{ background: transparent; border: none; }}
        QPushButton#workflowButton {{ padding: 0; text-align: left; }}
        QPushButton#workflowButton[suggested="true"] {{ border: 1px solid {accent}; background: {soft}; }}
        QPushButton#workflowButton QLabel {{ background: transparent; border: none; }}
        QLabel#workflowCaption {{ font-weight: 600; color: {text}; }}
        QLabel#taskGuide {{ color: {muted}; padding: 4px 0; }}
        QToolButton {{ background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 8px 18px 8px 10px; }}
        QToolButton:hover {{ background: {raised}; }}
        QMenu {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 6px; }}
        QMenu::item {{ padding: 8px 22px; }}
        QMenu::item:selected {{ background: {selected}; }}
        QPushButton#ghost:hover {{ background: {selected}; }}
        QPushButton#playButton {{ border-radius: 24px; min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px; padding: 0; background: {accent}; border: none; }}
        QTableView, QTableWidget {{ background: {panel}; border: 1px solid {border}; border-radius: 10px; gridline-color: {border}; selection-background-color: {selected}; selection-color: {text}; }}
        QTableView::item {{ padding: 6px; border: none; }}
        QTableView::item:hover {{ background: {raised}; }}
        QHeaderView::section {{ background: {raised}; color: {muted}; padding: 12px 8px; border: none; border-bottom: 1px solid {border}; font-weight: 600; }}
        QTableCornerButton::section {{ background: {raised}; border: none; }}
        QListWidget, QTextEdit, QPlainTextEdit, QScrollArea {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
        QListWidget::item {{ padding: 5px 8px; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {selected}; color: {text}; }}
        QListWidget#navigation {{ background: transparent; border: none; outline: none; padding: 0; }}
        QListWidget#navigation::item {{ padding: 12px 12px; margin: 2px 0; border-radius: 9px; }}
        QListWidget#navigation::item:selected {{ background: {soft}; color: {accent}; font-weight: 600; }}
        QListWidget#navigation::item:hover {{ background: {raised}; }}
        QScrollArea#detailScroll, QScrollArea#settingsScroll {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QGroupBox {{ border: 1px solid {border}; border-radius: 10px; margin-top: 15px; padding: 15px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {muted}; }}
        QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {muted}; border-radius: 3px; background: {panel}; }}
        QCheckBox::indicator:checked {{ background: #86DDBA; border-color: #86DDBA; image: url("{check_image}"); }}
        QProgressBar {{ border: none; background: {raised}; border-radius: 5px; min-height: 10px; text-align: center; }}
        QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {accent}; border-radius: 6px; width: 12px; margin: -4px 0; }}
        QSplitter::handle {{ background: transparent; width: 10px; }}
        QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 7px; }}
    ''')
