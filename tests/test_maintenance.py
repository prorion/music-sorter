import json
from pathlib import Path

import pytest

from music_sorter.maintenance import (apply_restore, export_settings, migrate_copy, prepare_restore, verify_database)
from music_sorter.scanner import scan_library
from music_sorter.settings import Settings


def test_restore_preserves_before_backup_and_requires_rescan(library, root, song, fake_reader, tmp_path):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    saved = tmp_path / 'saved.sqlite3'
    library.backup(saved)
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    original = song.read_bytes()
    plan = prepare_restore(library, saved)
    assert library.track(track['id'])['classification']['major']['value'] == '가요'
    result = apply_restore(library, plan)
    assert Path(result['backup']).is_file() and library.track(track['id'])['file_state'] == 'unavailable'
    assert library.track(track['id'])['review_state'] == 'unclassified' and song.read_bytes() == original
    scan_library(library, root)
    assert library.track(track['id'])['file_state'] == 'ready'


def test_restore_stale_copy_or_remote_request_is_blocked(library, root, song, fake_reader, tmp_path):
    scan_library(library, root)
    saved = tmp_path / 'saved.sqlite3'
    library.backup(saved)
    plan = prepare_restore(library, saved)
    Path(plan['staged']).write_bytes(b'changed')
    with pytest.raises(ValueError):
        apply_restore(library, plan)
    with library.connection(write=True) as db:
        db.execute("INSERT INTO llm_requests(id,job_id,target_ids,state,reserved,created_at) VALUES ('r','j','[]','unknown',100,'now')")
    with pytest.raises(ValueError, match='비용'):
        prepare_restore(library, saved)


def test_migration_copy_preserves_original_and_changes_only_backup_reference(library, root, song, fake_reader, tmp_path):
    scan_library(library, root)
    result = migrate_copy(library, Settings(music_root=str(root)), tmp_path / 'migrated')
    copied = Path(result['destination']) / 'music-sorter.sqlite3'
    assert verify_database(copied)['tracks'] == 1 and library.track(library.list_tracks()[0][0]['id'])
    assert json.loads((copied.parent / 'migration.json').read_text())['state'] == 'verified_copy'
    with pytest.raises(ValueError):
        migrate_copy(library, Settings(), copied.parent)


def test_settings_export_removes_contact_paths_workspace(tmp_path):
    target = tmp_path / 'export.json'
    settings = Settings(music_root='C:/private/music', musicbrainz_contact='private@example.test', anthropic_workspace_id='private-workspace')
    values = export_settings(settings, target)
    assert 'private' not in target.read_text('utf-8') and 'music_root' not in values


def test_foreign_root_and_future_schema_are_rejected(library, tmp_path):
    other = type(library)(tmp_path / 'other' / 'music.sqlite3')
    other.bind_root(tmp_path)
    with pytest.raises(ValueError, match='루트'):
        prepare_restore(library, other.path)
    with other.connection(write=True) as db:
        db.execute('PRAGMA user_version=999')
    with pytest.raises(ValueError, match='버전'):
        verify_database(other.path)


def test_activate_verified_migration_changes_only_next_default_location(library, tmp_path, monkeypatch):
    from music_sorter.settings import activate_data_directory, data_directory
    base = tmp_path / 'default'
    monkeypatch.setattr('music_sorter.settings.default_data_directory', lambda: base)
    assert data_directory() == base
    destination = tmp_path / 'migrated'
    migrate_copy(library, Settings(), destination)
    activate_data_directory(destination)
    assert data_directory() == destination and library.path.is_file()
    with (destination / 'music-sorter.sqlite3').open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='변경'):
        activate_data_directory(destination)
