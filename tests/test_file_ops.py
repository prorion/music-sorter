import hashlib
import json
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1, TCON, PRIV, USLT, APIC

from music_sorter.file_ops import FileOperations, inside_root, safe_name
from music_sorter.scanner import scan_library
from music_sorter.tag_io import digest, read_layout, write_replacement, genre_tag
from conftest import fake_snapshot


@pytest.fixture
def tagged(root, monkeypatch):
    path = root / "가수 - 제목.mp3"
    path.write_bytes(b"audio-payload" * 100 + b"TAG" + bytes(125))
    tag = ID3()
    for frame in (TIT2(text=["제목"]), TPE1(text=["가수"]), TCON(text=["Old Genre"]),
                  PRIV(owner="melon", data=b"\x00\xffsecret-private-data"),
                  USLT(encoding=3, lang="kor", desc="", text="기존 가사"),
                  APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"image-content")):
        tag.add(frame)
    tag.save(path, v2_version=4, v1=0)
    monkeypatch.setattr("music_sorter.scanner.read_snapshot", fake_snapshot)
    monkeypatch.setattr("music_sorter.file_ops.read_snapshot", fake_snapshot)
    return path


def confirmed(library):
    track = library.list_tracks()[0][0]
    library.save_manual(track["id"], dict(major="가요", subgenre=["발라드"], vocal="보컬", mood=["잔잔한"], concept=[]), track["revision"])
    return library.track(track["id"])


def test_tag_bytes_and_all_other_frames_preserved(tagged, tmp_path):
    original = read_layout(tagged)
    output = tmp_path / "changed.mp3"
    write_replacement(tagged, output, original.offset, genre_tag(original, "가요"))
    changed = read_layout(output)
    assert changed.version == original.version
    assert [x for x in changed.frames if x[0] != b"TCON"] == [x for x in original.frames if x[0] != b"TCON"]
    assert digest(tagged, original.offset) == digest(output, changed.offset)
    assert ID3(output).getall("TCON")[0].text == ["가요"]


def test_apply_and_exact_undo_preserve_id_and_classification(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    before = tagged.read_bytes()
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], organize=True, rename=True, write_genre=True)
    op = engine.operations(job)[0]
    assert tagged.read_bytes() == before
    assert engine.apply(job) == {"completed": 1, "blocked": 0}
    applied = library.track(track["id"])
    assert Path(applied["path"]).parent.name == "가요" and not tagged.exists()
    assert applied["hash"] != track["hash"]
    assert applied["classification"] == track["classification"]
    assert Path(engine.operations(job)[0]["result"]["original_tag"]).exists()
    engine.undo(op["id"])
    assert tagged.read_bytes() == before
    assert library.track(track["id"])["hash"] == track["hash"]
    assert library.track(track["id"])["classification"] == track["classification"]
    assert engine.operations(job)[0]["state"] == "undone"


def test_stale_preview_and_destination_collision_block(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], organize=True, write_genre=True)
    library.save_manual(track["id"], {"major": "팝", "subgenre": ["발라드"]}, track["revision"])
    assert engine.apply(job)["blocked"] == 1 and tagged.exists()
    track = library.track(track["id"])
    job = engine.preview([track["id"]], organize=True)
    destination = Path(engine.operations(job)[0]["plan"]["destination"])
    destination.parent.mkdir()
    destination.write_bytes(b"user-file")
    assert engine.apply(job)["blocked"] == 1
    assert destination.read_bytes() == b"user-file" and tagged.exists()


def test_undo_external_change_and_backup_corruption_block(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], write_genre=True)
    engine.apply(job)
    operation = engine.operations(job)[0]
    applied = tagged.read_bytes()
    tagged.write_bytes(applied + b"external")
    with pytest.raises(ValueError, match="외부 변경"):
        engine.undo(operation["id"])
    tagged.write_bytes(applied)
    Path(operation["result"]["original_tag"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="무결성"):
        engine.undo(operation["id"])
    assert tagged.read_bytes() == applied


def test_crash_after_file_done_recovers_db_without_repeating(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], organize=True, write_genre=True)
    def crash(stage):
        if stage == "file_done":
            raise RuntimeError("simulated process crash")
    with pytest.raises(RuntimeError):
        engine.apply(job, fault=crash)
    operation = engine.operations(job)[0]
    assert operation["state"] == "file_done"
    assert library.track(track["id"])["path"] == str(tagged)
    assert engine.recover() == {"recovered": 1, "pending": []}
    assert library.track(track["id"])["path"] == operation["plan"]["destination"]
    assert engine.recover()["recovered"] == 0
    engine.undo(operation["id"])
    assert tagged.exists()


def test_undo_crash_recovers_original_db(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    original = tagged.read_bytes()
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], organize=True, write_genre=True)
    engine.apply(job)
    def crash(stage):
        raise RuntimeError("simulated undo crash")
    with pytest.raises(RuntimeError):
        engine.undo(engine.operations(job)[0]["id"], fault=crash)
    assert tagged.read_bytes() == original
    assert engine.recover() == {"recovered": 1, "pending": []}
    assert library.track(track["id"])["hash"] == track["hash"]


@pytest.mark.parametrize('stage', ['prepared', 'tag_done'])
def test_explicit_resume_intermediate_stage(library, root, tagged, stage):
    scan_library(library, root)
    track = confirmed(library)
    engine = FileOperations(library, root)
    job = engine.preview([track['id']], organize=True, write_genre=True)
    def crash(actual):
        if actual == stage:
            raise RuntimeError('simulated crash')
    with pytest.raises(RuntimeError):
        engine.apply(job, fault=crash)
    assert engine.recover()['pending']
    assert engine.resume(job) == {'completed': 1, 'blocked': 0}
    assert Path(library.track(track['id'])['path']).parent.name == '가요'
    assert len(list(engine.rollback.rglob('original.id3'))) == 1


def test_kept_duplicate_decision_survives_program_tag_edits(library, root, tagged):
    import shutil
    other = root / 'copy' / tagged.name
    other.parent.mkdir()
    shutil.copy2(tagged, other)
    scan_library(library, root)
    for track in library.list_tracks()[0]:
        library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=[]), track['revision'])
    group = library.duplicate_groups()[0]
    ids = [track['id'] for track in group['tracks']]
    library.decide_duplicates(group['signature'], 'keep', ids)
    engine = FileOperations(library, root)
    job = engine.preview(ids, organize=True, write_genre=True)
    assert engine.apply(job) == {'completed': 2, 'blocked': 0}
    assert not library.duplicate_groups()
    for operation in engine.operations(job):
        engine.undo(operation['id'])
    assert not library.duplicate_groups()


def test_unresolved_keeps_existing_genre(library, root, tagged):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track["id"], {"major": "가요"}, track["revision"])
    original = tagged.read_bytes()
    engine = FileOperations(library, root)
    job = engine.preview([track["id"]], organize=True, write_genre=True)
    assert engine.operations(job)[0]["plan"]["genre"] is None
    assert engine.apply(job)["completed"] == 1
    destination = Path(library.track(track["id"])["path"])
    assert destination.parent.name == "_미확정" and destination.read_bytes() == original


def test_backup_limit_blocks_tags_before_original_change(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    original = tagged.read_bytes()
    engine = FileOperations(library, root, limit_gib=0)
    job = engine.preview([track["id"]], write_genre=True)
    assert engine.apply(job)["blocked"] == 1 and tagged.read_bytes() == original


def test_later_operation_undo_includes_dependencies(library, root, tagged):
    scan_library(library, root)
    track = confirmed(library)
    original = tagged.read_bytes()
    engine = FileOperations(library, root)
    job1 = engine.preview([track["id"]], organize=True, write_genre=True)
    engine.apply(job1)
    first = engine.operations(job1)[0]
    current = library.track(track["id"])
    library.save_manual(track["id"], {"major": "팝", "subgenre": ["발라드"]}, current["revision"])
    job2 = engine.preview([track["id"]], organize=True, write_genre=True)
    engine.apply(job2)
    assert len(engine.undo_preview(first["id"])) == 2
    engine.undo(first["id"])
    assert tagged.read_bytes() == original
    assert library.track(track["id"])["classification"]["major"]["value"] == "팝"


@pytest.mark.parametrize("name", ["CON", "nul.txt", "LPT1", "COM2.mp3", "CON.foo"])
def test_windows_reserved_names(name):
    assert safe_name(name).startswith("_")


def test_invalid_names_and_root_escape(root):
    assert safe_name('a/b:c*?"<>|. ') == "a_b_c______"
    with pytest.raises(ValueError, match="루트 밖"):
        inside_root(root, root / ".." / "outside.mp3")


def test_unsupported_header_blocks_tag_only(tagged):
    data = bytearray(tagged.read_bytes())
    data[5] = 0x80
    tagged.write_bytes(data)
    with pytest.raises(ValueError, match="헤더"):
        read_layout(tagged)
