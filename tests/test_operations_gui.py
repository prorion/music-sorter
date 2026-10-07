from pathlib import Path

from music_sorter.scanner import scan_library
from music_sorter.settings import Settings
from music_sorter.ui.operations import FileDialog
from music_sorter.ui.playlists import PlaylistDialog


def test_file_preview_controls_and_invalidation(qtbot, library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    dialog = FileDialog(library, Settings(music_root=str(root)), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.apply_button.isEnabled()
    dialog.checks['organize'].setChecked(True)
    dialog.preview()
    assert dialog.busy() and not dialog.checks['organize'].isEnabled()
    qtbot.waitUntil(lambda: not dialog.busy(), timeout=10000)
    assert dialog.apply_button.isEnabled() and dialog.table.rowCount() == 1
    assert song.exists() and not (root / '_미분류').exists()
    dialog.checks['rename'].setChecked(True)
    assert dialog.job_id is None and not dialog.apply_button.isEnabled() and dialog.table.rowCount() == 0


def test_playlist_gui_generates_relative_review_list(qtbot, library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    dialog = PlaylistDialog(library, Settings(music_root=str(root)))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert dialog.table.rowCount() == 2
    assert song.name.encode('utf-8') in (root / '[검토] 미확정 곡.m3u8').read_bytes()
    assert dialog.generate.isEnabled()


def test_playlist_outputs_are_paged(qtbot, library, root):
    from music_sorter.database import now
    with library.connection(write=True) as db:
        db.executemany('INSERT INTO playlist_outputs(path_key,path,definition,hash,dirty,updated_at) VALUES (?,?,?,?,?,?)',
                       [(str(root / f'{i:03}.m3u'), str(root / f'{i:03}.m3u'), '{}', 'fixture', 0, now()) for i in range(205)])
    dialog = PlaylistDialog(library, Settings(music_root=str(root)))
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 200 and dialog.next.isEnabled()
    dialog.turn_page(1)
    assert dialog.table.rowCount() == 5 and not dialog.next.isEnabled() and dialog.previous.isEnabled()


def test_playlist_gui_reuses_combination_once_across_formats(qtbot, library, root, song, fake_reader):
    from music_sorter.playlists import PlaylistExporter
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=['새벽']), track['revision'])
    custom = [{'name': '새벽', 'filters': {'mood': '잔잔한'}}]
    PlaylistExporter(library, root, file_format='m3u').export(custom)
    PlaylistExporter(library, root).export(custom)
    before = (root / '[조합] 새벽.m3u').read_bytes()
    dialog = PlaylistDialog(library, Settings(music_root=str(root)))
    qtbot.addWidget(dialog)
    dialog.start()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert dialog.status.text().startswith('생성 ') and library.jobs()[0]['state'] == 'completed'
    assert (root / '[조합] 새벽.m3u').read_bytes() == before
    assert (root / '[조합] 새벽.m3u8').read_bytes().count(song.name.encode('utf-8')) == 1


def test_external_cached_review_pages_cover_all_selected_tracks(qtbot, library, root, fake_reader):
    from music_sorter.ui.external_dialog import ExternalDialog
    for i in range(205):
        (root / f'가수 {i:03} - 곡.mp3').write_bytes(f'fixture {i}'.encode())
    scan_library(library, root)
    ids = [track['id'] for track in library.list_tracks(limit=500)[0]]
    dialog = ExternalDialog(library, Settings(), ids, {})
    qtbot.addWidget(dialog)
    assert len(dialog.rows) == 200 and dialog.next.isEnabled()
    dialog.turn_page(1)
    assert len(dialog.rows) == 200 and dialog.next.isEnabled()
    dialog.turn_page(1)
    assert len(dialog.rows) == 10 and not dialog.next.isEnabled()
    assert dialog.table.item(0, 2).text() == '미조회'


def test_uncertain_request_confirmation_gui_releases_only_explicit_selection(qtbot, library, root, song, fake_reader):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton, QTableWidget
    from music_sorter.ui.classify_dialog import ClassifyDialog
    from test_classifier import Client, prepared
    engine, job, ids = prepared(library, root, song, fake_reader)
    engine.run(job, Client(lambda *_: 'unknown'))
    dialog = ClassifyDialog(library, Settings(), ids, {}, job_id=job)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.resolve_button.isEnabled()
    def confirm():
        modal = QApplication.activeModalWidget()
        modal.findChild(QTableWidget).item(0, 0).setCheckState(Qt.CheckState.Checked)
        modal.findChild(QCheckBox).setChecked(True)
        modal.findChild(QLineEdit).setText('fixture provider confirmed no charge or processing')
        next(button for button in modal.findChildren(QPushButton) if button.text() == '확인 기록 저장·예약 해제').click()
    QTimer.singleShot(100, confirm)
    dialog.resolve_unknown()
    assert engine.summary(job)['reserved'] == 0 and not dialog.resolve_button.isEnabled()


def test_saved_classification_scope_does_not_follow_current_selection(qtbot, library, root, song, fake_reader):
    from music_sorter.scanner import scan_library
    from music_sorter.classifier import Classifier
    from music_sorter.ui.classify_dialog import ClassifyDialog
    second = root / 'second.mp3'
    second.write_bytes(b'second-song')
    scan_library(library, root)
    ids = [row['id'] for row in library.list_tracks()[0]]
    assert len(ids) == 2
    engine = Classifier(library)
    job = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget=1, execution='batch')
    dialog = ClassifyDialog(library, Settings(), ids[:1], {}, job_id=job)
    qtbot.addWidget(dialog)
    assert dialog.scope.currentText() == '작업 대상 2곡' and not dialog.scope.isEnabled()
    assert dialog.execution.currentData() == 'batch'
    assert dialog.status.text() == '저장된 작업 · 미제출 2곡'
    assert engine.summary(job)['actual'] == 0
