import shutil
import threading

import pytest

from music_sorter.database import Library
from music_sorter.scanner import scan_library


def pending_pair(library, root, song):
    scan_library(library, root)
    original = library.list_tracks()[0][0]
    library.save_manual(original["id"], {"major": "가요", "concept": []}, original["revision"])
    for name in ("one.mp3", "two.mp3"):
        shutil.copy2(song, root / name)
    song.unlink()
    scan_library(library, root)
    pending = next(row for row in library.list_tracks()[0] if row["file_state"] == "link_pending")
    return library.track(original["id"]), pending


@pytest.mark.parametrize("inherit", [True, False])
def test_manual_link_keeps_identity_and_archives_temporary_record(library, root, song, fake_reader, inherit):
    original, pending = pending_pair(library, root, song)
    assert library.link_candidates(pending["id"])[1] == 1
    result_id = library.resolve_link(pending["id"], pending["revision"], original["id"], original["revision"], inherit)
    assert result_id == original["id"]
    current = library.track(result_id)
    assert current["path"] == pending["path"] and current["file_state"] == "ready"
    assert current["classification"]["major"]["protected"] is inherit
    assert library.track(pending["id"])["file_state"] == "replaced"
    scan_library(library, root)
    assert library.track(pending["id"])["file_state"] == "replaced"
    assert library.list_tracks()[1] == 2
    with library.connection() as db:
        assert db.execute("SELECT inherit FROM file_links").fetchone()[0] == int(inherit)


def test_link_blocks_returned_original_changed_file_and_stale_revision(library, root, song, fake_reader):
    original, pending = pending_pair(library, root, song)
    song.write_bytes(b"original-audio")
    with pytest.raises(ValueError, match="존재"):
        library.resolve_link(pending["id"], pending["revision"], original["id"], original["revision"], True)
    song.unlink()
    with pytest.raises(ValueError, match="기존 후보"):
        library.resolve_link(pending["id"], pending["revision"], original["id"], original["revision"] - 1, True)
    from pathlib import Path
    Path(pending["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="재스캔"):
        library.resolve_link(pending["id"], pending["revision"])
    assert library.track(original["id"])["file_state"] == "missing"


def test_link_as_new_is_explicit_and_cancel_preserves_pending(library, root, song, fake_reader):
    original, pending = pending_pair(library, root, song)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        library.resolve_link(pending["id"], pending["revision"], cancel=cancel)
    assert library.track(pending["id"])["file_state"] == "link_pending"
    result = library.resolve_link(pending["id"], pending["revision"])
    assert result == pending["id"] and library.track(result)["file_state"] == "ready"
    assert library.track(original["id"])["file_state"] == "missing"


def test_schema_upgrade_backs_up_and_preserves_classification(library, root, song, fake_reader):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    library.save_manual(row["id"], {"concept": []}, row["revision"])
    with library.connection(write=True) as db:
        db.execute("DROP TABLE file_links")
        db.execute("DROP TABLE bulk_targets")
        db.execute("PRAGMA user_version=1")
    upgraded = Library(library.path)
    backups = list((library.path.parent / "backups").glob("before-schema-2-*.sqlite3"))
    assert len(backups) == 1
    with upgraded.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert upgraded.track(row["id"])["classification"]["concept"]["protected"]
    import sqlite3
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    Library(library.path)
    assert len(list((library.path.parent / "backups").glob("*.sqlite3"))) == 1


def test_bulk_filtered_snapshot_covers_more_than_one_page_and_protects_axes(library, root, song, fake_reader):
    for i in range(205):
        (root / f"가수 - 곡 {i:03d}.mp3").write_bytes(str(i).encode())
    scan_library(library, root)
    preview = library.preview_bulk({"mood": {"mode": "replace", "value": ["잔잔한"]}}, "filtered", {"search": "곡"})
    assert preview["total"] == preview["eligible"] == 205
    assert len(library.bulk_rows(preview["job_id"])) == 200
    assert len(library.bulk_rows(preview["job_id"], 200)) == 5
    assert library.apply_bulk(preview["job_id"])["applied"] == 205
    assert library.list_tracks(mood="잔잔한")[1] == 205
    rows = library.list_tracks(mood="잔잔한")[0]
    assert all(row["classification"]["mood"]["protected"] for row in rows)
    assert all(row["classification"]["major"]["status"] == "unclassified" for row in rows)
    with pytest.raises(ValueError):
        library.apply_bulk(preview["job_id"])


def test_bulk_skips_changed_revision_or_path_after_preview(library, root, song, fake_reader):
    other = root / "가수 - 두번째.mp3"
    other.write_bytes(b"different")
    scan_library(library, root)
    rows = library.list_tracks()[0]
    preview = library.preview_bulk({"major": {"mode": "set", "value": "가요"}}, "all")
    library.save_manual(rows[0]["id"], {"major": "팝"}, rows[0]["revision"])
    old_path = rows[1]["path"]
    from pathlib import Path
    Path(old_path).rename(root / "다른 경로.mp3")
    scan_library(library, root)
    result = library.apply_bulk(preview["job_id"])
    assert result["applied"] == 0 and result["stale"] == 2
    assert library.track(rows[0]["id"])["classification"]["major"]["value"] == "팝"
    assert library.jobs()[0]["state"] == "completed"  # Most recent job is the re-scan.


def test_bulk_conflicting_parent_is_withheld_and_explicit_reset_is_allowed(library, root, song, fake_reader):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    library.save_manual(row["id"], {"major": "재즈", "subgenre": ["스윙/빅밴드"]}, row["revision"])
    preview = library.preview_bulk({"major": {"mode": "set", "value": "가요"}}, "all")
    assert preview["blocked"] == 1 and preview["eligible"] == 0
    assert library.apply_bulk(preview["job_id"])["applied"] == 0
    assert library.track(row["id"])["classification"]["subgenre"]["value"] == ["스윙/빅밴드"]
    preview = library.preview_bulk({"major": {"mode": "set", "value": "가요"}, "subgenre": {"mode": "unknown"}}, "all")
    assert library.apply_bulk(preview["job_id"])["applied"] == 1
    assert library.track(row["id"])["classification"]["subgenre"]["protected"]


def test_bulk_tag_actions_do_not_infer_unknown_and_cancel_keeps_committed_chunk(library, root, song, fake_reader):
    for i in range(201):
        (root / f"가수 - {i:03d}.mp3").write_bytes(str(i).encode())
    scan_library(library, root)
    preview = library.preview_bulk({"concept": {"mode": "add", "value": ["카페"]}}, "all")
    assert preview["blocked"] == 202
    preview = library.preview_bulk({"concept": {"mode": "none"}}, "all")
    cancel = threading.Event()
    result = library.apply_bulk(preview["job_id"], cancel, lambda *_: cancel.set())
    assert result == dict(applied=200, stale=0, cancelled=2)
    assert sum(row["classification"]["concept"]["protected"] for row in library.list_tracks(limit=500)[0]) == 200
    assert library.jobs()[0]["state"] == "cancelled"
    assert song.read_bytes() == b"original-audio"


def test_bulk_add_remove_preserve_other_tags_and_block_cardinality_overflow(library, root, song, fake_reader):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    library.save_manual(row["id"], {"mood": ["잔잔한", "감성적인"], "concept": ["카페", "봄"]}, row["revision"])
    preview = library.preview_bulk({"concept": {"mode": "add", "value": ["카페", "드라이브"]}}, "all")
    library.apply_bulk(preview["job_id"])
    assert library.track(row["id"])["classification"]["concept"]["value"] == ["카페", "봄", "드라이브"]
    preview = library.preview_bulk({"concept": {"mode": "remove", "value": ["카페"]}}, "all")
    library.apply_bulk(preview["job_id"])
    assert library.track(row["id"])["classification"]["concept"]["value"] == ["봄", "드라이브"]
    preview = library.preview_bulk({"mood": {"mode": "add", "value": ["경쾌한"]}}, "all")
    assert preview["blocked"] == 1
    assert library.track(row["id"])["classification"]["mood"]["value"] == ["잔잔한", "감성적인"]
