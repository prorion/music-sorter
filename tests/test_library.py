import shutil
import threading

import pytest

from music_sorter.database import Library, path_key
from music_sorter.scanner import ScanControl, scan_library


def first(library):
    return library.list_tracks()[0][0]


def test_rescan_copy_and_move_preserve_manual(library, root, song, fake_reader):
    scan_library(library, root)
    original = first(library)
    library.save_manual(original["id"], {"major": "가요", "concept": []}, original["revision"])
    copy = root / "copy" / song.name
    copy.parent.mkdir()
    shutil.copy2(song, copy)
    scan_library(library, root)
    rows, total = library.list_tracks()
    assert total == 2 and len({row["id"] for row in rows}) == 2
    assert library.track(original["id"])["classification"]["concept"]["protected"]
    assert library.track(original["id"])["classification"]["concept"]["value"] == []
    # Once an equal-hash source survives, a later new copy cannot steal the missing ID.
    moved = root / "moved.mp3"
    song.rename(moved)
    result = scan_library(library, root)
    assert result["moved"] == 0
    assert library.track(original["id"])["file_state"] == "missing"


def test_unique_move_preserves_identity_and_manual(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    library.save_manual(old["id"], {"vocal": "보컬"}, old["revision"])
    moved = root / "renamed.mp3"
    song.rename(moved)
    result = scan_library(library, root)
    assert result["moved"] == 1
    current = library.track(old["id"])
    assert current["path"] == str(moved.resolve())
    assert current["classification"]["vocal"]["protected"]


def test_ambiguous_moves_do_not_merge(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    shutil.copy2(song, root / "one.mp3")
    shutil.copy2(song, root / "two.mp3")
    song.unlink()
    result = scan_library(library, root)
    assert result["pending"] == 2
    assert library.track(old["id"])["file_state"] == "missing"
    assert sum(t["file_state"] == "link_pending" for t in library.list_tracks()[0]) == 2
    scan_library(library, root)
    assert sum(t["file_state"] == "link_pending" for t in library.list_tracks()[0]) == 2


def test_external_change_blocks_edits_and_requires_fresh_review(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    library.save_manual(old["id"], {"major": "가요"}, old["revision"])
    song.write_bytes(b"changed-audio")
    scan_library(library, root)
    changed = library.track(old["id"])
    assert changed["file_state"] == "external_change" and changed["hash"] == old["hash"]
    with pytest.raises(ValueError):
        library.save_manual(old["id"], {"major": "팝"}, changed["revision"])
    song.write_bytes(b"changed-again")
    with pytest.raises(ValueError):
        library.accept_external_change(old["id"], inherit_manual=True)
    scan_library(library, root)
    library.accept_external_change(old["id"], inherit_manual=True)
    assert library.track(old["id"])["classification"]["major"]["value"] == "가요"
    assert library.track(old["id"])["file_state"] == "ready"


def test_replacement_gets_new_id_and_archives_previous(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    song.write_bytes(b"other-song")
    scan_library(library, root)
    new_id = library.accept_external_change(old["id"], inherit_manual=False, as_new=True)
    scan_library(library, root)
    assert new_id != old["id"]
    assert library.track(old["id"])["file_state"] == "replaced"
    assert library.track(new_id)["file_state"] == "ready"


def test_failed_directory_does_not_mark_files_missing(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    job = library.start_job()
    library.finish_scan(job, root, True, [path_key(root)])
    assert library.track(old["id"])["file_state"] == "unavailable"


def test_cancelled_scan_never_marks_missing(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    song.rename(root / "move.mp3")
    control = ScanControl()
    control.cancelled.set()
    assert scan_library(library, root, control=control)["cancelled"]
    assert library.track(old["id"])["file_state"] == "ready"
    assert library.jobs()[0]["state"] == "cancelled"


def test_nonrecursive_scan_does_not_mark_children_missing(library, root, song, fake_reader):
    child = root / "child"
    child.mkdir()
    shutil.copy2(song, child / song.name)
    scan_library(library, root)
    scan_library(library, root, recursive=False)
    assert all(t["file_state"] == "ready" for t in library.list_tracks()[0])


def test_manual_validation_is_atomic_and_unlock_retains_value(library, root, song, fake_reader):
    scan_library(library, root)
    track = first(library)
    with pytest.raises(ValueError):
        library.save_manual(track["id"], {"major": "가요", "subgenre": ["스윙/빅밴드"]}, track["revision"])
    assert library.track(track["id"])["revision"] == 0
    library.save_manual(track["id"], {"major": "가요", "subgenre": ["발라드"], "concept": []}, 0)
    with pytest.raises(ValueError):
        library.save_manual(track["id"], {"mood": ["잔잔한"]}, 0)
    library.unlock(track["id"], "subgenre")
    current = library.track(track["id"])["classification"]
    assert current["major"]["protected"] and not current["subgenre"]["protected"]
    assert current["subgenre"]["value"] == ["발라드"]


def test_duplicate_decision_and_backup_preserve_records(library, root, song, fake_reader, tmp_path):
    shutil.copy2(song, root / ("copy-" + song.name))
    copy_dir = root / "copy"
    copy_dir.mkdir()
    shutil.copy2(song, copy_dir / song.name)
    scan_library(library, root)
    group = library.duplicate_groups()[0]
    ids = [t["id"] for t in group["tracks"]]
    library.decide_duplicates(group["signature"], "keep", ids)
    assert not library.duplicate_groups()
    destination = tmp_path / "backup.sqlite3"
    library.backup(destination)
    assert Library(destination).list_tracks()[1] == library.list_tracks()[1]
    assert song.read_bytes() == b"original-audio"
    with pytest.raises(ValueError):
        library.backup(destination)


def test_search_is_literal_and_page_limit_is_bounded(library, root, song, fake_reader):
    (root / "a - 100%_test.mp3").write_bytes(b"percent")
    scan_library(library, root)
    rows, count = library.list_tracks(search="%_")
    assert count == 1 and rows[0]["title"] == "100%_test"
    assert len(library.list_tracks(limit=1)[0]) == 1


def test_second_root_and_newer_schema_are_rejected(library, root, tmp_path):
    library.bind_root(root)
    another = tmp_path / "second"
    another.mkdir()
    with pytest.raises(ValueError):
        library.bind_root(another)
    with library.connection(write=True) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError):
        Library(library.path)


def test_disconnected_root_is_unavailable_not_missing(library, root, song, fake_reader):
    scan_library(library, root)
    old = first(library)
    root.rename(root.with_name("disconnected"))
    with pytest.raises(ValueError):
        scan_library(library, root)
    assert library.track(old["id"])["file_state"] == "unavailable"


def test_restart_records_interruption_without_starting_scan(library, root):
    job_id = library.start_job()
    library.recover_interrupted_jobs()
    jobs = library.jobs()
    assert jobs[0]["id"] == job_id and jobs[0]["state"] == "interrupted"
    assert library.list_tracks()[1] == 0


def test_unreadable_equal_hash_source_blocks_move_inference(library, root, song, fake_reader):
    from conftest import fake_snapshot
    other = root / "other.mp3"
    shutil.copy2(song, other)
    scan_library(library, root)
    original = next(t for t in library.list_tracks()[0] if t["path"] == str(song.resolve()))
    new = root / "new.mp3"
    song.rename(new)
    job = library.start_job()
    library.observe(job, other, None, "PermissionError")
    library.observe(job, new, fake_snapshot(new))
    result = library.finish_scan(job, root, True, [])
    assert result["moved"] == 0 and result["pending"] == 1
    assert library.track(original["id"])["file_state"] == "missing"
