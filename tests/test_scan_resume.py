import os

import pytest

from music_sorter.scanner import ScanControl, scan_library


def test_excluded_folder_does_not_mark_existing_tracks_missing(library, root, song, fake_reader):
    folder = root / 'excluded'
    folder.mkdir()
    copy = folder / '가수 - 다른곡.mp3'
    copy.write_bytes(b'other-audio')
    scan_library(library, root)
    excluded = next(row for row in library.list_tracks()[0] if row['path'] == str(copy.resolve()))
    copy.unlink()
    result = scan_library(library, root, exclude=['excluded'])
    assert result['processed'] == 1 and result['missing'] == 0
    assert library.track(excluded['id'])['file_state'] == 'ready'
    with pytest.raises(ValueError):
        scan_library(library, root, exclude=['../outside'])


def test_resume_reuses_matching_metadata_after_full_hash_check(library, root, song, fake_reader, monkeypatch):
    control = ScanControl()
    from conftest import fake_snapshot
    def read_then_cancel(path, cancel=None):
        payload = fake_snapshot(path)
        control.cancelled.set()
        return payload
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', read_then_cancel)
    interrupted = scan_library(library, root, control=control)
    assert interrupted['cancelled'] and library.list_tracks()[1] == 0
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', lambda *_: pytest.fail('동일 파일의 태그를 다시 읽으면 안 됨'))
    result = scan_library(library, root, resume_job=interrupted['job_id'])
    assert result['new'] == 1 and result['job_id'] == interrupted['job_id']


def test_resume_detects_same_stat_content_change_and_deleted_checkpoint(library, root, song, fake_reader, monkeypatch):
    control = ScanControl()
    from conftest import fake_snapshot
    def read_then_cancel(path, cancel=None):
        payload = fake_snapshot(path)
        control.cancelled.set()
        return payload
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', read_then_cancel)
    plan = scan_library(library, root, control=control)
    state = song.stat()
    song.write_bytes(b'changed--audio')
    assert song.stat().st_size == state.st_size
    os.utime(song, ns=(state.st_atime_ns, state.st_mtime_ns))
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', fake_snapshot)
    result = scan_library(library, root, resume_job=plan['job_id'])
    assert result['new'] == 1 and library.list_tracks()[0][0]['hash'] == fake_snapshot(song)['hash']
    control = ScanControl()
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', read_then_cancel)
    plan = scan_library(library, root, control=control)
    song.unlink()
    result = scan_library(library, root, resume_job=plan['job_id'])
    assert result['missing'] == 1 and result['processed'] == 0


def test_changed_resume_scope_is_rejected(library, root, song, fake_reader):
    control = ScanControl()
    control.cancelled.set()
    plan = scan_library(library, root, control=control)
    with pytest.raises(ValueError, match='범위'):
        scan_library(library, root, recursive=False, resume_job=plan['job_id'])
