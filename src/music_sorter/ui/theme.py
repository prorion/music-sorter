from PySide6.QtGui import QColor, QPalette
from pathlib import Path


def apply_theme(app, theme: str, font_scale: float):
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
    app.setStyleSheet(f'''
        QWidget {{ color: {text}; }}
        QMainWindow, QDialog, QWidget#workspace {{ background: {background}; }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#muted, QLabel#subtle {{ color: {muted}; }}
        QLabel#subtle {{ font-size: 11px; }}
        QLabel#pageTitle {{ font-size: 25px; font-weight: 700; }}
        QLabel#brand {{ font-size: 19px; font-weight: 700; letter-spacing: 1px; }}
        QLabel#statValue {{ font-size: 27px; font-weight: 700; }}
        QFrame#statCard, QWidget#detailPanel, QFrame#playerBar, QWidget#sidebar {{ background: {panel}; border: 1px solid {border}; border-radius: 14px; }}
        QWidget#sidebar {{ border: none; }}
        QWidget#trackEditor {{ background: {panel}; }}
        QFrame#statCard[tint="mint"] QLabel#statValue {{ color: {accent}; }}
        QLabel#badge {{ color: {accent}; background: {soft}; border-radius: 7px; padding: 5px 10px; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px 11px; min-height: 21px; selection-background-color: {soft}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
        QComboBox::drop-down {{ width: 22px; border: none; }}
        QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {border}; selection-background-color: {selected}; padding: 6px; }}
        QPushButton {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px 13px; min-height: 20px; }}
        QPushButton:hover {{ background: {raised}; border-color: {muted}; }}
        QPushButton:pressed {{ background: {selected}; }}
        QPushButton:focus {{ border: 1px solid {accent}; }}
        QPushButton:disabled {{ color: {muted}; border-color: {border}; background: {raised}; }}
        QPushButton[primary="true"] {{ background: {accent}; color: {'#10251E' if dark else '#FFFFFF'}; border: 1px solid {accent}; font-weight: 600; }}
        QPushButton[primary="true"]:disabled {{ background: {raised}; color: {muted}; border: 1px solid {border}; }}
        QPushButton#ghost {{ background: transparent; border: none; }}
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
