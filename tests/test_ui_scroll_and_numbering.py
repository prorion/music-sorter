from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from music_sorter.scanner import scan_library
from music_sorter.settings import Settings
from music_sorter.ui.main_window import MainWindow, TrackModel
from music_sorter.ui.theme import apply_theme
from music_sorter.ui.classify_dialog import ClassifyDialog


def wheel(widget, delta=-120):
    center = widget.rect().center()
    event = QWheelEvent(QPointF(center), QPointF(widget.mapToGlobal(center)), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_closed_and_editable_combos_keep_value_and_scroll_parent(qtbot, qapp):
    apply_theme(qapp, 'dark', 1)
    scroll = QScrollArea()
    scroll.resize(480, 260)
    content = QWidget()
    layout = QVBoxLayout(content)
    combos = []
    for editable in (False, True):
        combo = QComboBox()
        combo.setEditable(editable)
        combo.addItems(['첫째', '둘째', '셋째'])
        combo.setCurrentIndex(1)
        layout.addWidget(combo)
        combos.append(combo)
    layout.addWidget(QLabel('아래 내용'))
    layout.addSpacing(1500)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.waitExposed(scroll)
    for combo in combos:
        scroll.verticalScrollBar().setValue(0)
        combo.setFocus()
        wheel(combo.lineEdit() if combo.isEditable() else combo)
        assert combo.currentIndex() == 1
        assert scroll.verticalScrollBar().value() > 0
    scroll.verticalScrollBar().setValue(0)
    combos[0].setFocus()
    qtbot.keyClick(combos[0], Qt.Key.Key_Down)
    assert combos[0].currentIndex() == 2
    combos[0].addItems([f'항목 {i}' for i in range(100)])
    combos[0].showPopup()
    assert combos[0].view().isVisible()
    wheel(combos[0].view().viewport())
    assert combos[0].view().verticalScrollBar().value() > 0
    combos[0].hidePopup()


def test_numbering_follows_pages_filters_and_sort_without_changing_track_identity(qtbot, library, root, fake_reader, tmp_path):
    for i in range(205):
        (root / f'아티스트 - 곡 {i:03}.mp3').write_bytes(str(i).encode())
    scan_library(library, root)
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / 'settings.json')
    qtbot.addWidget(window)
    model = window.model
    assert model.headers[0] == 'No.' and model.columnCount() == 7
    assert model.data(model.index(0, 0)) == 1
    assert model.data(model.index(199, 0)) == 200
    assert model.data(model.index(0, 1)) == model.rows[0]['title']
    window.turn_page(1)
    assert model.data(model.index(0, 0)) == 201 and model.data(model.index(4, 0)) == 205
    model.sort(1, Qt.SortOrder.DescendingOrder)
    assert window.sort_column == 'title_key' and window.descending
    assert model.data(model.index(0, 0)) == 1 and model.rows[0]['title'].endswith('204')
    window.search.setText('곡 000')
    window.filters_changed()
    assert model.rowCount() == 1 and model.data(model.index(0, 0)) == 1
    track_id = model.rows[0]['id']
    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert model.rows[0]['id'] == track_id
    assert model.data(model.index(0, 2)) == model.rows[0]['artist']


def test_saved_classification_readable_details_do_not_submit_requests(qtbot, library, root, song, fake_reader, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox, QTabWidget
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    dialog = ClassifyDialog(library, Settings(music_root=str(root)), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table_stack.currentWidget() == dialog.empty_note
    assert dialog.collect_button.isHidden() and dialog.resolve_button.isHidden()
    dialog.job_id = dialog.engine.prepare([track['id']], provider='anthropic', model='claude-haiku-5-5')
    dialog.load()
    assert dialog.table_stack.currentWidget() == dialog.table
    assert dialog.table.item(0, 0).text() == '1'
    assert dialog.run_button.isEnabled() and dialog.engine.summary(dialog.job_id)['actual'] == 0
    details = []

    def inspect_modal():
        modal = QApplication.activeModalWidget()
        tabs = modal.findChild(QTabWidget)
        details.append((tabs.tabText(0), tabs.widget(0).toPlainText(), tabs.widget(1).toPlainText()))
        modal.accept()

    QTimer.singleShot(50, inspect_modal)
    dialog.inspect(0, 1)
    assert details[0][0] == '곡 정보·분류 결과'
    assert track['title'] in details[0][1] and '가사: 보내지 않음' in details[0][1]
    assert '"input"' in details[0][2]
    assert dialog.engine.summary(dialog.job_id)['actual'] == 0 and song.read_bytes() == b'original-audio'
