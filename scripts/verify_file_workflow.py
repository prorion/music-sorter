"""Real MP3 copies: scan -> fixture classification -> apply -> export -> exact undo.

Fixture classifications are intentionally synthetic; this is not an accuracy evaluation.
Original music is only read and hashed. Every mutation stays in the fresh output directory.
"""
import argparse
import json
import shutil
from pathlib import Path

from mutagen.id3 import ID3

from music_sorter.database import Library
from music_sorter.file_ops import FileOperations
from music_sorter.playlists import PlaylistExporter
from music_sorter.scanner import scan_library
from music_sorter.tag_io import digest, read_layout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=128)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or source in output.parents or output.exists():
        raise ValueError('새 검증 폴더를 선택하세요. 원본 루트 안에서는 쓰지 않습니다.')
    output.mkdir(parents=True)
    originals = sorted(source.rglob('*.mp3'))
    hashes = {str(path): digest(path) for path in originals}
    root = output / 'sample-library'
    root.mkdir()
    for i, path in enumerate(originals[:args.count]):
        destination = root / f'{i:04}' / path.name
        destination.parent.mkdir()
        shutil.copy2(path, destination)
    library = Library(output / 'user-data' / 'library.sqlite3')
    scan = scan_library(library, root)
    tracks, total = library.list_tracks(limit=500)
    fixtures = dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=['새벽'])
    for track in tracks:
        library.save_manual(track['id'], fixtures, track['revision'])
    for group in library.duplicate_groups():
        library.decide_duplicates(group['signature'], 'distinct', [t['id'] for t in group['tracks']])
    before = {t['id']: {'hash': t['hash'], 'path': t['path']} for t in tracks}
    engine = FileOperations(library, root)
    job = engine.preview(list(before), organize=True, rename=True, write_genre=True)
    application = engine.apply(job)
    assert application == {'completed': total, 'blocked': 0}, application
    checked = 0
    for op in engine.operations(job):
        if not op['plan']['genre']:
            continue
        path = Path(library.track(op['track_id'])['path'])
        result = op['result']
        assert digest(path, result['tag_offset']) == result['body_hash']
        assert ID3(path).getall('TCON')[0].text == ['가요']
        checked += 1
    playlists = PlaylistExporter(library, root).export([{'name': '복사본 검증', 'filters': {'major': '가요', 'mood': '잔잔한'}}])
    exported = (root / '[조합] 복사본 검증.m3u8').read_bytes()
    assert exported.count(b'\r\n') == total + 1
    assert str(root).encode('utf-8') not in exported
    for op in reversed(engine.operations(job)):
        engine.undo(op['id'])
    for track_id, prior in before.items():
        current = library.track(track_id)
        assert current['path'] == prior['path'] and current['hash'] == prior['hash']
        assert digest(Path(current['path'])) == prior['hash']
        assert current['classification']['major']['protected']
    rescan = scan_library(library, root)
    assert rescan['changed'] == 0 and rescan['new'] == 0
    assert all(digest(Path(path)) == value for path, value in hashes.items())
    report = dict(original_files=len(originals), originals_unchanged=True, copies=total,
                  scan=scan, fixture_classification=True, classification_accuracy_evaluated=False,
                  application=application, genre_checked=checked, playlists=playlists,
                  exact_undo=total, rescan=rescan)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
