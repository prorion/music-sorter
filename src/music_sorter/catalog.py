"""Versioned additive taxonomy; retirement never destroys existing decisions."""
import json
import os
import unicodedata
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from .classification import TAXONOMY
from .database import now


def load_catalog(path):
    path = Path(path)
    if path.exists():
        if path.stat().st_size > 512 * 1024:
            raise ValueError('분류 목록 파일이 너무 큽니다.')
        result = json.loads(path.read_text('utf-8'))
    else:
        result = json.loads(files('music_sorter').joinpath('resources/taxonomy.json').read_text('utf-8'))
    validate_catalog(result)
    return result


def validate_catalog(catalog):
    original = json.loads(files('music_sorter').joinpath('resources/taxonomy.json').read_text('utf-8'))
    if type(catalog.get('version')) is not int or catalog['version'] < 1 or catalog['major'].keys() != original['major'].keys() or catalog['vocal'] != original['vocal']:
        raise ValueError('대분류·보컬 구분은 첫 버전에서 고정입니다.')
    groups = list(catalog['major'].values()) + [catalog['mood'], catalog['concept']]
    for values in groups:
        if not isinstance(values, list) or not values or len(values) > 200 or any(not isinstance(v, str) or not v.strip() or len(v) > 60 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError('태그 목록·문자·개수를 확인하세요.')
        if len(set(values)) != len(values):
            raise ValueError('같은 목록의 중복 태그를 확인하세요.')
    # Bundled labels remain valid for stored history and protected values.
    for major, values in original['major'].items():
        if not set(values) <= set(catalog['major'][major]):
            raise ValueError('기존 태그는 삭제 대신 사용 중단으로 보존하세요.')
    for axis in ('mood', 'concept'):
        if not set(original[axis]) <= set(catalog[axis]):
            raise ValueError('기존 태그를 삭제할 수 없습니다.')
    if not isinstance(catalog.get('retired', {}), dict) or not isinstance(catalog.get('history', []), list):
        raise ValueError('목록 변경 기록을 확인하세요.')
    for group, values in catalog.get('retired', {}).items():
        if group not in {'mood', 'concept'} and not group.startswith('subgenre:'):
            raise ValueError('사용 중단할 수 없는 분류 항목입니다.')
        allowed = catalog['major'].get(group.removeprefix('subgenre:')) if group.startswith('subgenre:') else catalog.get(group)
        if not isinstance(allowed, list) or not isinstance(values, list) or not set(values) <= set(allowed) or len(set(values)) != len(values):
            raise ValueError('사용 중단 태그의 참조를 확인하세요.')
        if set(values) == set(allowed):
            raise ValueError('각 목록에는 사용 가능한 태그가 하나 이상 필요합니다.')


def active_catalog(catalog=None):
    result = deepcopy(TAXONOMY if catalog is None else catalog)
    retired = result.pop('retired', {})
    result.pop('history', None)
    for group, values in retired.items():
        if group.startswith('subgenre:'):
            major = group.removeprefix('subgenre:')
            result['major'][major] = [v for v in result['major'][major] if v not in values]
        else:
            result[group] = [v for v in result[group] if v not in values]
    return result


def options(axis, major=None, current=None):
    active = active_catalog()
    values = list(active['major'].get(major, [])) if axis == 'subgenre' else list(active[axis])
    previous = current if isinstance(current, list) else [current] if current else []
    return list(dict.fromkeys(values + previous))


def change_catalog(path, group, name, action, expected_version):
    catalog = load_catalog(path)
    if catalog['version'] != expected_version:
        raise ValueError('목록 버전이 바뀌었습니다. 다시 열어 주세요.')
    name = unicodedata.normalize('NFC', name.strip())
    values = catalog['major'].get(group.removeprefix('subgenre:')) if group.startswith('subgenre:') else catalog.get(group) if group in {'mood', 'concept'} else None
    if not isinstance(values, list) or not name or len(name) > 60 or any(ord(c) < 32 for c in name):
        raise ValueError('태그 종류·이름을 확인하세요.')
    retired = catalog.setdefault('retired', {}).setdefault(group, [])
    if action == 'add' and name not in values:
        values.append(name)
    elif action == 'retire' and name in values and name not in retired:
        retired.append(name)
    elif action == 'reactivate' and name in retired:
        retired.remove(name)
    else:
        raise ValueError('이미 등록·처리했거나 지원하지 않는 변경입니다.')
    catalog['version'] += 1
    catalog.setdefault('history', []).append(dict(group=group, tag=name, action=action, at=now(), version=catalog['version']))
    validate_catalog(catalog)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.taxonomy-{uuid4().hex}.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(catalog, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return catalog
