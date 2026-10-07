from PySide6.QtGui import QColor, QPalette


def apply_theme(app, theme: str, font_scale: float):
    dark = theme == "dark" or (theme == "system" and app.styleHints().colorScheme().name == "Dark")
    background, panel, text, border, accent = ("#111827", "#1F2937", "#F3F4F6", "#374151", "#2DD4BF") if dark else (
        "#F5F7FA", "#FFFFFF", "#17212B", "#D8DEE6", "#0F766E")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, background), (QPalette.ColorRole.Base, panel),
                        (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Text, text),
                        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.Button, panel),
                        (QPalette.ColorRole.Highlight, accent), (QPalette.ColorRole.HighlightedText, "#111827" if dark else "#FFFFFF")):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = app.font()
    font.setFamily("맑은 고딕")
    font.setPointSizeF(11 * font_scale)
    app.setFont(font)
    app.setStyleSheet(f"""
        QMainWindow, QDialog {{ background: {background}; color: {text}; }}
        QLabel {{ color: {text}; }}
        QLineEdit, QComboBox, QListWidget, QTableView, QTextEdit, QScrollArea {{
            background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 5px;
        }}
        QLineEdit, QComboBox {{ padding: 6px; min-height: 22px; }}
        QPushButton {{ background: {panel}; color: {text}; border: 1px solid {border};
            border-radius: 5px; padding: 6px 12px; min-height: 22px; }}
        QPushButton:hover {{ border-color: {accent}; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {accent}; }}
        QPushButton:disabled {{ color: #7A8491; }}
        QPushButton[primary="true"] {{ background: {accent}; color: {"#111827" if dark else "#FFFFFF"}; border: none; }}
        QHeaderView::section {{ background: {background}; color: {text}; padding: 8px; border: none; border-bottom: 1px solid {border}; }}
        QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 12px; padding: 12px; color: {text}; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; }}
        QListWidget::item {{ padding: 8px; }}
        QListWidget::item:selected {{ background: {accent}; color: {"#111827" if dark else "#FFFFFF"}; }}
        QProgressBar {{ border: 1px solid {border}; border-radius: 4px; text-align: center; min-height: 20px; color: {text}; }}
        QProgressBar::chunk {{ background: {accent}; }}
        QSplitter::handle {{ background: {border}; }}
        QToolTip {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 4px; }}
    """)
