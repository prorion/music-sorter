"""Create a private, grouped review corpus; never turn model output into gold labels."""
import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from music_sorter.database import Library, normalized
from music_sorter.scanner import read_snapshot, scan_library
from music_sorter.tag_io import digest


def group_records(pool):
    parent = list(range(len(pool)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    hashes, identities = {}, {}
    for index, track in enumerate(pool):
        identity = (normalized(track['artist']), normalized(track['title']), track['version'])
        for mapping, key in [(hashes, track['hash']), (identities, identity)]:
            if mapping is identities and not all(identity[:2]):
                continue
            if key in mapping:
                parent[find(index)] = find(mapping[key])
            else:
                mapping[key] = index
    grouped = defaultdict(list)
    for index, track in enumerate(pool):
        grouped[find(index)].append(track)
    return {hashlib.sha256('|'.join(sorted(set(t['hash'] for t in tracks))).encode()).hexdigest(): tracks
            for tracks in grouped.values()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, choices=[200], default=200)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError('원본 밖의 새 검증 폴더를 사용하세요.')
    pool, errors = [], Counter()
    for path in sorted(source.rglob('*.mp3')):
        try:
            pool.append(read_snapshot(path))
        except (OSError, ValueError) as error:
            errors[type(error).__name__] += 1
    # Group identical bytes and same credited recording/version; choose representatives.
    groups = group_records(pool)
    buckets = defaultdict(list)
    for group, tracks in groups.items():
        track = sorted(tracks, key=lambda t: (t['grade'], -(t['bitrate'] or 0), t['path']))[0]
        hints = track['title'] + ' ' + track['artist']
        # Strata are candidate-selection clues, not genre truth.
        stratum = 'ccm_clue' if any(word in hints for word in ('주님', '찬송', '예수', '찬양', '워십')) else 'classical_clue' if any(word.casefold() in hints.casefold() for word in ('Chopin', 'Bach', 'Beethoven', 'Mozart', 'Concerto', 'Sonata')) else 'version_clue' if track['version'] else 'other'
        buckets[(track['grade'], stratum)].append((group, track))
    rng = random.Random(20261008)
    for values in buckets.values():
        rng.shuffle(values)
    selected = []
    while len(selected) < args.count and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(selected) < args.count:
                selected.append(buckets[key].pop())
    if len(selected) != args.count:
        raise ValueError('검토 후보 수가 부족합니다.')
    rng.shuffle(selected)
    assert len({track['hash'] for _, track in selected}) == len(selected)
    output.mkdir(parents=True)
    root = output / 'review-music'
    root.mkdir()
    manifest = []
    for index, (group, track) in enumerate(selected):
        folder = root / f'{index:04}'
        folder.mkdir()
        copied = folder / Path(track['path']).name
        shutil.copy2(track['path'], copied)
        if digest(copied) != track['hash']:
            raise ValueError('검토 사본의 해시가 일치하지 않습니다.')
        manifest.append(dict(group=group, hash=track['hash'], split='tune' if index < 120 else 'holdout',
                             title=track['title'], artist=track['artist'], album=track['album'], version=track['version'], grade=track['grade'],
                             path=str(copied.relative_to(root)), reviewed=False,
                             classification={axis: {'status': 'unreviewed', 'value': None} for axis in ('major', 'subgenre', 'vocal', 'mood', 'concept')}))
    library = Library(output / 'user-data' / 'music-sorter.sqlite3')
    scan_library(library, root)
    from music_sorter.settings import Settings
    Settings(music_root=str(root)).save(output / 'user-data' / 'settings.json')
    with (output / 'review-candidates.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        fields = ['split', 'group', 'title', 'artist', 'album', 'version', 'grade', 'path']
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(manifest)
    (output / 'review-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
    report = dict(pool=len(pool), read_errors=dict(errors), groups=len(groups), selected=len(selected),
                  tune=120, holdout=80, graded=dict(Counter(row['grade'] for row in manifest)),
                  human_reviewed=0, classification_accuracy_evaluated=False, model_requests=0)
    (output / 'preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
