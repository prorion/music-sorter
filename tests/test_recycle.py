import json
import shutil
from pathlib import Path

import pytest

from music_sorter.recycle import DuplicateRemoval
from music_sorter.classification import TAXONOMY
from music_sorter.scanner import scan_library


def duplicates(library, root, song):
    copy = root / '사본' / song.name
    copy.parent.mkdir()
    shutil.copy2(song, copy)
    scan_library(library, root)
    return library.duplicate_groups()[0]


def fake_recycle(tmp_path):
    def move(path):
        target = tmp_path / 'fake-bin' / path.parent.name / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, target)
        return str(target)
    return move


def test_selected_file_removed_survivor_and_history_preserved_restore_by_rescan(library, root, song, fake_reader, tmp_path):
    group = duplicates(library, root, song)
    target, survivor = group['tracks']
    library.save_manual(target['id'], {'major': next(iter(TAXONOMY['major']))}, target['revision'])
    group = library.duplicate_groups()[0]
    target = next(item for item in group['tracks'] if item['id'] == target['id'])
    engine = DuplicateRemoval(library, root, recycle=fake_recycle(tmp_path))
    result = engine.apply(engine.preview(group, [target['id']]))
    assert result['processed'] == 1 and result['failed'] == 0
    assert not Path(target['path']).exists() and Path(survivor['path']).exists()
    current = library.track(target['id'])
    assert current['file_state'] == 'missing' and current['classification'] == target['classification']
    assert library.duplicate_groups() == []
    assert library.jobs()[0]['kind'] == 'duplicate_delete' and library.jobs()[0]['processed'] == 1
    assert list((library.path.parent / 'backups').glob('before-recycle-*.sqlite3'))
    shutil.move(result['targets'][0]['recycle_path'], target['path'])
    scan_library(library, root)
    assert library.track(target['id'])['file_state'] == 'ready'
    assert library.track(target['id'])['classification'] == target['classification']


def test_refuses_all_files_changed_contents_stale_plan_and_active_scan(library, root, song, fake_reader, tmp_path):
    group = duplicates(library, root, song)
    calls = []
    engine = DuplicateRemoval(library, root, recycle=lambda path: calls.append(path))
    with pytest.raises(ValueError, match='한 개 이상'):
        engine.preview(group, [item['id'] for item in group['tracks']])
    target = group['tracks'][0]
    plan = engine.preview(group, [target['id']])
    Path(group['tracks'][1]['path']).write_bytes(b'externally modified')
    with pytest.raises(ValueError, match='파일 내용'):
        engine.apply(plan)
    assert calls == [] and Path(target['path']).exists()
    scan_library(library, root)
    with pytest.raises(ValueError, match='그룹이 변경'):
        engine.apply(plan)
    # An unchanged new group still cannot race an active scan.
    Path(group['tracks'][1]['path']).write_bytes(b'original-audio')
    scan_library(library, root)
    new_group = library.duplicate_groups()[0]
    library.start_job('scan')
    with pytest.raises(ValueError, match='스캔'):
        engine.preview(new_group, [new_group['tracks'][0]['id']])


def test_partial_failure_and_interrupted_sending_never_retried(library, root, song, fake_reader, tmp_path):
    duplicates(library, root, song)
    third = root / '세번째' / song.name
    third.parent.mkdir()
    shutil.copy2(song, third)
    scan_library(library, root)
    group = library.duplicate_groups()[0]
    targets = [item['id'] for item in group['tracks'][:2]]
    calls = []
    move = fake_recycle(tmp_path)
    def recycle(path):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError('파일 잠금')
        return move(path)
    engine = DuplicateRemoval(library, root, recycle=recycle)
    result = engine.apply(engine.preview(group, targets))
    assert result['processed'] == result['failed'] == 1
    assert library.jobs()[0]['state'] == 'partial'
    assert result['targets'][1]['state'] == 'unknown'
    assert Path(result['targets'][1]['path']).exists()
    engine.recover()
    assert len(calls) == 2


def test_crash_after_move_journal_survives_and_no_auto_delete(library, root, song, fake_reader, tmp_path):
    group = duplicates(library, root, song)
    calls = []
    move = fake_recycle(tmp_path)
    def interrupted(path):
        calls.append(path)
        move(path)
        raise SystemExit('crash after native move')
    engine = DuplicateRemoval(library, root, recycle=interrupted)
    with pytest.raises(SystemExit):
        engine.apply(engine.preview(group, [group['tracks'][0]['id']]))
    library.recover_interrupted_jobs()
    engine.recover()
    assert len(calls) == 1 and library.jobs()[0]['state'] == 'interrupted'
    with library.connection() as db:
        journal = json.loads(db.execute("SELECT value FROM metadata WHERE key LIKE 'duplicate_delete:%'").fetchone()[0])
    assert journal['targets'][0]['state'] == 'unknown'
    assert Path(group['tracks'][1]['path']).exists()


def test_refuses_db_revision_change_after_confirmation(library, root, song, fake_reader, tmp_path):
    group = duplicates(library, root, song)
    engine = DuplicateRemoval(library, root, recycle=fake_recycle(tmp_path))
    plan = engine.preview(group, [group['tracks'][0]['id']])
    target = group['tracks'][0]
    library.save_manual(target['id'], {'major': next(iter(TAXONOMY['major']))}, target['revision'])
    with pytest.raises(ValueError, match='DB 상태'):
        engine.apply(plan)
    assert all(Path(item['path']).exists() for item in group['tracks'])
