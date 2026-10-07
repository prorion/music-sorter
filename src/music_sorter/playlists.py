"""Owned playlist exports with relative Windows/Android-friendly paths."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .classification import TAXONOMY
from .database import encode, now, path_key
from .file_ops import duplicate_policy, inside_root, safe_name
from .scanner import ScanControl
from .tag_io import digest


def definitions(custom=None):
    result = []
    for major, children in TAXONOMY['major'].items():
        for tag in children:
            result.append({'name': f'[장르] {major} - {safe_name(tag)}', 'filters': {'major': major, 'subgenre': tag}})
    for axis, label in (('mood', '분위기'), ('concept', '컨셉')):
        for tag in TAXONOMY[axis]:
            result.append({'name': f'[{label}] {safe_name(tag)}', 'filters': {axis: tag}})
    for vocal in TAXONOMY['vocal']:
        result.append({'name': f'[CCM] {vocal}', 'filters': {'major': 'CCM', 'vocal': vocal}})
    result += [{'name': '[검토] 미확정 곡', 'review': 'unresolved'}, {'name': '[검토] 중복 후보', 'review': 'duplicates'}]
    for item in custom or []:
        if not isinstance(item, dict) or not item.get('filters') or any(axis not in {'major', 'subgenre', 'vocal', 'mood', 'concept'} for axis in item['filters']):
            raise ValueError('조합 재생목록의 분류 조건을 확인하세요.')
        result.append({'name': '[조합] ' + safe_name(item['name']), 'filters': item['filters']})
    return result


def matches(track, definition, policy):
    state = policy.get('state')
    if definition.get('review') == 'duplicates':
        return state == 'pending'
    if state == 'discarded':
        return False
    if definition.get('review') == 'unresolved':
        return track['review_state'] == 'unresolved'
    if track['review_state'] != 'confirmed' or state == 'pending':
        return False
    for axis, required in definition.get('filters', {}).items():
        value = track['classification'][axis]['value']
        required = required if isinstance(required, list) else [required]
        values = value if isinstance(value, list) else [value]
        if any(item not in values for item in required):
            return False
    return True


class PlaylistExporter:
    def __init__(self, library, root: Path, tolerance=3.0, *, file_format='m3u8'):
        if file_format not in {'m3u8', 'm3u'}:
            raise ValueError('재생목록 출력 형식을 확인하세요.')
        self.library, self.root, self.tolerance = library, root.absolute(), tolerance
        self.extension = '.' + file_format

    def preview(self, custom=None, control=None):
        self.library.bind_root(self.root)
        control = control or ScanControl()
        policies = duplicate_policy(self.library, self.tolerance)
        output = [{**item, 'paths': []} for item in definitions(custom)]
        offset = skipped = 0
        while True:
            rows, _ = self.library.list_tracks(limit=200, offset=offset, sort='title_key')
            if not rows:
                break
            for track in rows:
                control.checkpoint()
                try:
                    path = inside_root(self.root, Path(track['path']))
                    if track['file_state'] != 'ready' or digest(path) != track['hash']:
                        skipped += 1
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    if '\n' in relative or '\r' in relative:
                        skipped += 1
                        continue
                except (OSError, ValueError):
                    skipped += 1
                    continue
                for item in output:
                    if matches(track, item, policies.get(track['id'], {})):
                        item['paths'].append(relative)
            offset += len(rows)
        with self.library.connection() as db:
            owned = {row['path_key']: dict(row) for row in db.execute('SELECT * FROM playlist_outputs')}
        reserved = set()
        for item in output:
            path = inside_root(self.root, self.root / (item['name'] + self.extension))
            original = owned.get(path_key(path))
            item['previous_hash'] = original['hash'] if original else None
            item['collision'] = path.exists() and (original is None or digest(path) != original['hash'])
            if item['collision']:
                alias = next((entry for entry in owned.values()
                              if json.loads(entry['definition'])['name'] == item['name']
                              and Path(entry['path']).suffix.lower() == self.extension
                              and Path(entry['path']).exists() and digest(Path(entry['path'])) == entry['hash']
                              and path_key(entry['path']) != path_key(path)), None)
                if alias:
                    path = inside_root(self.root, Path(alias['path']))
                    item['previous_hash'] = alias['hash']
                    item['path'] = str(path)
                    item['bytes'] = ('#EXTM3U\r\n' + ''.join(p + '\r\n' for p in item['paths'])).encode('utf-8')
                    item['count'] = len(item['paths'])
                    reserved.add(path_key(path))
                    continue
                candidate = path.with_name(path.stem + ' [music-sorter]' + self.extension)
                suffix = 2
                while candidate.exists() or path_key(candidate) in reserved:
                    candidate = path.with_name(path.stem + f' [music-sorter {suffix}]' + self.extension)
                    suffix += 1
                path = candidate
                item['previous_hash'] = None
            if path_key(path) in reserved:
                raise ValueError('같은 이름의 조합 재생목록이 있습니다. 다른 이름을 사용하세요.')
            reserved.add(path_key(path))
            item['path'] = str(path)
            item['bytes'] = ('#EXTM3U\r\n' + ''.join(p + '\r\n' for p in item['paths'])).encode('utf-8')
            item['count'] = len(item['paths'])
        # Empty standard lists are written only when previously owned; review lists always exist.
        output = [item for item in output if item['count'] or item.get('review') or item['previous_hash']]
        return {'outputs': output, 'skipped': skipped}

    def export(self, custom=None, control=None, progress=None):
        control, progress = control or ScanControl(), progress or (lambda *_: None)
        job_id = self.library.start_job('playlists')
        count = blocked = 0
        try:
            with self.library._write_lock:
                plan = self.preview(custom, control)
                for item in plan['outputs']:
                    control.checkpoint()
                    path = inside_root(self.root, Path(item['path']))
                    # Never adopt or replace a playlist edited after preview.
                    if path.exists() and (not item['previous_hash'] or digest(path) != item['previous_hash']):
                        blocked += 1
                        continue
                    temporary = path.with_name('.music-sorter-playlist-' + uuid4().hex + '.tmp')
                    with temporary.open('xb') as stream:
                        stream.write(item['bytes'])
                        stream.flush()
                        os.fsync(stream.fileno())
                    expected = hashlib.sha256(item['bytes']).hexdigest()
                    if digest(temporary) != expected:
                        raise ValueError('재생목록 저장 검증 실패')
                    if item['previous_hash'] and path.exists():
                        if digest(path) != item['previous_hash']:
                            blocked += 1
                            continue
                        os.replace(temporary, path)
                    else:
                        os.rename(temporary, path)
                    definition = {key: item[key] for key in ('name', 'filters', 'review') if key in item}
                    with self.library.connection(write=True) as db:
                        db.execute('INSERT OR REPLACE INTO playlist_outputs VALUES (?,?,?,?,0,?)',
                                   (path_key(path), str(path), expected, encode(definition), now()))
                    count += 1
                    progress(count, blocked)
        except InterruptedError:
            self.library.job_state(job_id, 'cancelled', '완료 재생목록 유지')
            return {'job_id': job_id, 'completed': count, 'blocked': blocked, 'cancelled': True}
        except Exception:
            self.library.job_state(job_id, 'failed', '재생목록 생성 실패. 완료 출력은 보존했습니다.')
            raise
        self.library.job_state(job_id, 'partial' if blocked else 'completed', f'출력 {count} · 충돌 {blocked} · 제외 곡 {plan["skipped"]}')
        with self.library.connection(write=True) as db:
            db.execute('UPDATE jobs SET processed=?,failed=? WHERE id=?', (count, blocked, job_id))
        return {'job_id': job_id, 'completed': count, 'blocked': blocked, 'skipped': plan['skipped']}
