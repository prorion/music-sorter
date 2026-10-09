from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QPushButton

from music_sorter.scanner import scan_library
from music_sorter.settings import Settings
from music_sorter.ui.main_window import MainWindow
from music_sorter.ui.playlists import PlaylistDialog
from music_sorter.ui.external_dialog import ExternalDialog


def test_workflow_routes_selected_songs_without_executing_tasks(qtbot, library, root, song, fake_reader, tmp_path, monkeypatch):
    (root / '두 번째.mp3').write_bytes(b'second')
    scan_library(library, root)
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / 'settings.json')
    qtbot.addWidget(window)
    window.show()
    window.resize(1000, 700)
    qtbot.wait(10)
    for button in window.workflow_buttons:
        assert button.rect().contains(button.caption.geometry())
        assert button.caption.height() >= button.caption.fontMetrics().height()
    selection = window.table.selectionModel()
    selection.select(window.model.index(1, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    expected = {row['id'] for row in window.model.rows}
    opened = []
    for module, name in (('external_dialog', 'ExternalDialog'), ('classify_dialog', 'ClassifyDialog'), ('operations', 'FileDialog')):
        def record(dialog, task=name):
            opened.append((task, set(dialog.selected), dialog.worker))
            return 0
        monkeypatch.setattr(f'music_sorter.ui.{module}.{name}.exec', record)
    before = len(library.jobs())
    for button in (window.external_button, window.classify_button, window.file_button):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(opened) == 3 and all(ids == expected and worker is None for _, ids, worker in opened)
    assert len(library.jobs()) == before and song.read_bytes() == b'original-audio'
    assert '선택 2곡' in window.selection_hint.text()
    playlists = []
    monkeypatch.setattr('music_sorter.ui.playlists.PlaylistDialog.exec', lambda dialog: playlists.append(dialog.settings.music_root) or 0)
    window.playlist_button.click()
    window.navigation.setCurrentRow(3)
    assert playlists == [str(root), str(root)] and window.navigation.currentRow() == 0
    window.navigation.setCurrentRow(4)
    assert window.pages.currentWidget() is window.jobs_table


def test_empty_classification_plan_returns_to_editable_options_without_submitting(qtbot, library, root, song, fake_reader):
    from music_sorter.ui.classify_dialog import ClassifyDialog
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=['새벽']), track['revision'])
    dialog = ClassifyDialog(library, Settings(music_root=str(root)), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.run()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    saved_job = dialog.job_id
    assert not dialog.engine.summary(saved_job)['counts'] and not dialog.new_plan_button.isHidden()
    dialog.new_plan_button.click()
    assert dialog.job_id is None and dialog.purpose.isEnabled()
    assert dialog.engine.summary(saved_job)['actual'] == 0 and song.read_bytes() == b'original-audio'


def test_review_entry_reveals_candidates_hidden_by_old_filters(qtbot, library, root, song, fake_reader, tmp_path):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / 'settings.json')
    qtbot.addWidget(window)
    window.search.setText('없는 검색어')
    window.state_filter.setCurrentIndex(window.state_filter.findData('confirmed'))
    window.filters_changed()
    assert window.model.rowCount() == 0
    window.open_review()
    assert window.navigation.currentRow() == 1 and window.model.rowCount() == 1
    assert not window.search.text() and not window.state_filter.currentData()
    assert window.details_button.isChecked()
    assert all(not button.isVisible() for button in window.editor.unlock_buttons.values() if button is not window.editor.unlock_buttons['major'])


def test_empty_library_only_enables_import_and_busy_scan_blocks_next_tasks(qtbot, library, root, song, fake_reader, tmp_path):
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / 'settings.json')
    qtbot.addWidget(window)
    window.show()
    assert window.detail_panel.isHidden() and not window.details_button.isEnabled()
    assert not window.editor.save_button.isEnabled()
    assert all(button.isHidden() for button in window.editor.unlock_buttons.values())
    assert window.selection_menu.isHidden()
    assert window.scan_button.isEnabled()
    assert all(not button.isEnabled() for button in window.workflow_buttons[1:])
    window.start_scan()
    assert all(not button.isEnabled() for button in window.workflow_buttons)
    qtbot.waitUntil(lambda: not window.worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: window.classify_button.isEnabled(), timeout=10000)
    assert not window.detail_panel.isHidden() and window.details_button.isEnabled()
    assert song.read_bytes() == b'original-audio'


def test_playlist_custom_mode_requires_name_and_conditions(qtbot, library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=['새벽']), track['revision'])
    dialog = PlaylistDialog(library, Settings(music_root=str(root)))
    qtbot.addWidget(dialog)
    assert dialog.custom_form.isHidden()
    dialog.custom_toggle.setChecked(True)
    dialog.filters['major'].setCurrentIndex(1)
    dialog.start()
    assert dialog.worker is None and '이름' in dialog.status.text()
    assert not list(root.glob('*.m3u8'))
    dialog.name.setText('가요 모음')
    dialog.filters['major'].setCurrentIndex(0)
    dialog.start()
    assert dialog.worker is None and '조건' in dialog.status.text()
    dialog.filters['major'].setCurrentIndex(1)
    dialog.start()
    assert dialog.worker is not None and not dialog.custom_form.isEnabled()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert (root / '[조합] 가요 모음.m3u8').exists() and song.read_bytes() == b'original-audio'


def test_lookup_dialog_has_one_start_and_no_implicit_enter_action(qtbot, qapp, library):
    from music_sorter.ui.theme import apply_theme
    apply_theme(qapp, 'dark', 1)
    dialog = ExternalDialog(library, Settings(), [], {})
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.start_button.text() == '실행' and dialog.stop.isHidden()
    assert not any(button.autoDefault() or button.isDefault() for button in dialog.findChildren(QPushButton))
    qtbot.keyClick(dialog, Qt.Key.Key_Return)
    assert dialog.worker is None and not library.jobs()
