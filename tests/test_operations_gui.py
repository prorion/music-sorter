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
    assert song.name.encode('utf-8') in (root / '[검토] 미확정 곡.m3u').read_bytes()
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
