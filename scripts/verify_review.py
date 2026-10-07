"""Exercise review operations with real MP3 copies and a private verification DB."""
import argparse
import hashlib
import json
import shutil
import threading
from pathlib import Path

from music_sorter.database import Library
from music_sorter.scanner import scan_library
from music_sorter.settings import Settings


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("검증 출력은 새 디렉터리를 지정하세요.")
    sources = sorted(args.source.rglob("*.mp3"))[:2]
    if len(sources) < 2:
        raise ValueError("서로 다른 MP3 샘플이 2개 이상 필요합니다.")
    source_hashes = {path: digest(path) for path in sources}
    root = output / "sample-library"
    root.mkdir(parents=True)
    originals = []
    for index, source in enumerate(sources):
        destination = root / f"original-{index}" / source.name
        destination.parent.mkdir()
        shutil.copy2(source, destination)
        originals.append(destination)
    library = Library(output / "user-data" / "music-sorter.sqlite3")
    scan_library(library, root)
    original = next(row for row in library.list_tracks()[0] if row["path"] == str(originals[0]))
    library.save_manual(original["id"], {"major": "가요", "concept": []}, original["revision"])
    copies = []
    for name in ("one", "two", "three"):
        destination = root / name / originals[0].name
        destination.parent.mkdir()
        shutil.copy2(originals[0], destination)
        copies.append(destination)
    assert originals[0].is_relative_to(output)
    originals[0].unlink()  # Only the disposable fixture, never the source music directory.
    scan_library(library, root)
    pending = [row for row in library.list_tracks()[0] if row["file_state"] == "link_pending"]
    assert len(pending) == 3
    previous = library.track(original["id"])
    result_id = library.resolve_link(pending[0]["id"], pending[0]["revision"], previous["id"], previous["revision"], True)
    assert result_id == original["id"]
    assert library.track(result_id)["classification"]["concept"]["protected"]
    library.resolve_link(pending[1]["id"], pending[1]["revision"])
    first = library.preview_bulk({"concept": {"mode": "none"}, "mood": {"mode": "replace", "value": ["잔잔한"]}}, "all")
    assert first["eligible"] == 3 and first["blocked"] == 1
    current = library.track(result_id)
    library.save_manual(result_id, {"mood": ["감성적인"]}, current["revision"])
    stale = library.apply_bulk(first["job_id"])
    assert stale["applied"] == 2 and stale["stale"] == 1
    second = library.preview_bulk({"concept": {"mode": "none"}, "mood": {"mode": "replace", "value": ["잔잔한"]}}, "all")
    applied = library.apply_bulk(second["job_id"])
    assert applied["applied"] == 1
    tags = library.preview_bulk({"concept": {"mode": "add", "value": ["카페"]}}, "filtered", {"mood": "잔잔한"})
    assert library.apply_bulk(tags["job_id"])["applied"] == 3
    assert library.list_tracks(mood="잔잔한", concept="카페")[1] == 3
    cancelled = library.preview_bulk({"major": {"mode": "set", "value": "가요"}}, "all")
    cancel = threading.Event()
    cancel.set()
    assert library.apply_bulk(cancelled["job_id"], cancel)["applied"] == 0
    scan_library(library, root)
    assert library.track(result_id)["classification"]["concept"]["value"] == ["카페"]
    current_hashes = [digest(path) == source_hashes[sources[0]] for path in copies]
    assert all(current_hashes) and digest(originals[1]) == source_hashes[sources[1]]
    sources_unchanged = all(digest(path) == expected for path, expected in source_hashes.items())
    assert sources_unchanged
    Settings(music_root=str(root)).save(output / "user-data" / "settings.json")
    library.backup(output / "backup.sqlite3")
    report = dict(link_preserved_id=True, linked_temporary_record_archived=True, manual_protection_survived=True,
                  new_file_choice_confirmed=True, stale_preview_withheld=stale, remaining_pending=1,
                  bulk_filtered_matches=3, source_music_unchanged=sources_unchanged, current_music_bytes_preserved=True)
    (output / "review-verification.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
