import json

import pytest

from music_sorter.scanner import scan_library


def test_restore_selected_history_preserves_later_entries_and_file(library, root, song, fake_reader):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    original = song.read_bytes()
    library.save_manual(row['id'], {'major': '가요'}, row['revision'])
    first = library.classification_history(row['id'])[0][0]
    library.save_manual(row['id'], {'mood': ['잔잔한']}, library.track(row['id'])['revision'])
    library.restore_classification(row['id'], first['id'], library.track(row['id'])['revision'])
    current = library.track(row['id'])
    assert current['review_state'] == 'unclassified' and current['revision'] == 3
    rows, total = library.classification_history(row['id'])
    assert total == 3 and rows[0]['action'] == f"restore_history:{first['id']}"
    assert json.loads(rows[1]['current'])['mood']['value'] == ['잔잔한']
    assert song.read_bytes() == original


def test_history_conflicts_and_foreign_track_block(library, root, song, fake_reader):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    library.save_manual(row['id'], {'major': '가요'}, row['revision'])
    history = library.classification_history(row['id'])[0][0]
    with pytest.raises(ValueError):
        library.restore_classification(row['id'], history['id'], row['revision'])
    with pytest.raises(ValueError):
        library.restore_classification('other', history['id'], 1)
    with library.connection(write=True) as db:
        db.execute("UPDATE tracks SET file_state='external_change' WHERE id=?", (row['id'],))
    with pytest.raises(ValueError):
        library.restore_classification(row['id'], history['id'], 1)


def test_reopen_bulk_only_unsubmitted_plan_can_apply(qtbot, library, root, song, fake_reader):
    from music_sorter.ui.bulk import BulkDialog
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    plan = library.preview_bulk({'major': {'mode': 'set', 'value': '가요'}}, track_ids=[row['id']])
    dialog = BulkDialog(library, {}, [], job_id=plan['job_id'])
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 1 and dialog.apply_button.isEnabled()
    library.apply_bulk(plan['job_id'])
    finished = BulkDialog(library, {}, [], job_id=plan['job_id'])
    qtbot.addWidget(finished)
    assert finished.model.rowCount() == 1 and not finished.apply_button.isEnabled()
