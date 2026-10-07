from pathlib import Path

import pytest

from music_sorter.file_ops import FileOperations
from music_sorter.playlists import PlaylistExporter
from music_sorter.scanner import scan_library


def test_relative_utf8_crlf_and_custom_and_filters(library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=['새벽']), track['revision'])
    exporter = PlaylistExporter(library, root)
    custom = [{'name': '새벽에 듣는 음악', 'filters': {'major': '가요', 'mood': '잔잔한', 'concept': '새벽'}},
              {'name': '다른 조건', 'filters': {'mood': '신나는'}}]
    preview = exporter.preview(custom)
    assert not list(root.glob('*.m3u8'))
    names = {item['name']: item for item in preview['outputs']}
    assert '[조합] 새벽에 듣는 음악' in names and '[조합] 다른 조건' not in names
    exporter.export(custom)
    path = root / '[조합] 새벽에 듣는 음악.m3u8'
    assert path.read_bytes() == ('#EXTM3U\r\n' + song.name + '\r\n').encode('utf-8')
    assert str(root).encode('utf-8') not in path.read_bytes()


def test_unresolved_only_in_review_and_missing_excluded(library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'mood': ['잔잔한']}, track['revision'])
    exporter = PlaylistExporter(library, root)
    exporter.export()
    assert not (root / '[분위기] 잔잔한.m3u8').exists()
    review = root / '[검토] 미확정 곡.m3u8'
    assert song.name.encode('utf-8') in review.read_bytes()
    song.unlink()
    scan_library(library, root)
    exporter.export()
    assert review.read_bytes() == b'#EXTM3U\r\n'


def test_user_playlist_and_modified_owned_playlist_preserved(library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    user = root / '[검토] 미확정 곡.m3u8'
    user.write_bytes(b'user-owned-playlist')
    exporter = PlaylistExporter(library, root)
    exporter.export()
    assert user.read_bytes() == b'user-owned-playlist'
    generated = root / '[검토] 미확정 곡 [music-sorter].m3u8'
    assert generated.exists()
    exporter.export()
    assert not (root / '[검토] 미확정 곡 [music-sorter 2].m3u8').exists()
    generated.write_bytes(b'user-edited')
    exporter.export()
    assert generated.read_bytes() == b'user-edited'
    assert (root / '[검토] 미확정 곡 [music-sorter 2].m3u8').exists()


def test_pending_duplicates_excluded_and_discarded_not_exported(library, root, song, fake_reader):
    other = root / 'copy' / song.name
    other.parent.mkdir()
    other.write_bytes(song.read_bytes())
    scan_library(library, root)
    for track in library.list_tracks()[0]:
        library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=[]), track['revision'])
    exporter = PlaylistExporter(library, root)
    exporter.export()
    assert not (root / '[분위기] 잔잔한.m3u8').exists()
    assert (root / '[검토] 중복 후보.m3u8').read_bytes().count(b'\r\n') == 3
    group = library.duplicate_groups()[0]
    kept = group['tracks'][0]
    library.decide_duplicates(group['signature'], 'keep', [kept['id']])
    exporter.export()
    contents = (root / '[분위기] 잔잔한.m3u8').read_bytes()
    assert contents == ('#EXTM3U\r\n' + Path(kept['path']).relative_to(root).as_posix() + '\r\n').encode('utf-8')


def test_moved_path_marks_dirty_and_export_refreshes(library, root, song, fake_reader, monkeypatch):
    from conftest import fake_snapshot
    monkeypatch.setattr('music_sorter.file_ops.read_snapshot', fake_snapshot)
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    exporter = PlaylistExporter(library, root)
    exporter.export()
    engine = FileOperations(library, root)
    job = engine.preview([track['id']], organize=True)
    engine.apply(job)
    with library.connection() as db:
        assert all(row[0] for row in db.execute('SELECT dirty FROM playlist_outputs'))
    exporter.export()
    assert '_미확정/'.encode('utf-8') in (root / '[검토] 미확정 곡.m3u8').read_bytes()
    with library.connection() as db:
        assert not any(row[0] for row in db.execute('SELECT dirty FROM playlist_outputs'))


def test_format_change_preserves_old_files_and_avoids_foreign_format_alias(library, root, song, fake_reader):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    library.save_manual(track['id'], {'major': '가요'}, track['revision'])
    legacy = PlaylistExporter(library, root, file_format='m3u')
    legacy.export()
    old = root / '[검토] 미확정 곡.m3u'
    before = old.read_bytes()
    # New-format collision must not fall back to a previously owned legacy path.
    user = root / '[검토] 미확정 곡.m3u8'
    user.write_bytes(b'user-owned')
    exporter = PlaylistExporter(library, root)
    exporter.export()
    alias = root / '[검토] 미확정 곡 [music-sorter].m3u8'
    assert alias.is_file() and song.name.encode('utf-8') in alias.read_bytes()
    assert old.read_bytes() == before and user.read_bytes() == b'user-owned'
    exporter.export()
    assert not (root / '[검토] 미확정 곡 [music-sorter 2].m3u8').exists()


def test_unsupported_playlist_format_is_blocked_before_writes(library, root):
    with pytest.raises(ValueError, match='형식'):
        PlaylistExporter(library, root, file_format='cp949')
    assert not list(root.iterdir())
