"""Explicit development profile parsing. Never reads .env or environment implicitly."""
import os
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .classifier import budget_micro


MAPPING = dict(UI_THEME=('theme', str), UI_FONT_SCALE=('font_scale', float), NOTIFY_ON_COMPLETION=('notify_on_completion', bool),
               MUSIC_ROOT=('music_root', str), SCAN_INCLUDE_SUBFOLDERS=('include_subfolders', bool),
               SCAN_EXCLUDE_FOLDERS=('scan_exclude_folders', json.loads),
               CLASSIFY_PROVIDER=('classify_provider', str), CLASSIFY_MODEL=('classify_model', str),
               ESCALATE_PROVIDER=('escalate_provider', str), ESCALATE_MODEL=('escalate_model', str),
               ANTHROPIC_WORKSPACE_ID=('anthropic_workspace_id', str), MUSICBRAINZ_ENABLED=('musicbrainz_enabled', bool),
               MUSICBRAINZ_CONTACT=('musicbrainz_contact', str), LASTFM_ENABLED=('lastfm_enabled', bool),
               DOMESTIC_SEARCH_ENABLED=('domestic_enabled', bool),
               DUPLICATE_DURATION_TOLERANCE_SECONDS=('duplicate_tolerance_seconds', float),
               ROLLBACK_STORAGE_LIMIT_GIB=('rollback_limit_gib', float), PLAYLIST_FORMAT=('playlist_format', str))
MAPPING.update(TRACKS_PER_REQUEST=('llm_tracks_per_request', int),
               LLM_MAX_OUTPUT_TOKENS_PER_TRACK=('llm_max_output_tokens_per_track', int),
               API_TIMEOUT_SECONDS=('llm_timeout_seconds', int), API_MAX_RETRIES=('llm_max_retries', int),
               CLASSIFY_INCLUDE_LYRICS=('include_lyrics_default', bool))
KEYS = dict(OPENAI_API_KEY='openai', ANTHROPIC_API_KEY='anthropic', LASTFM_API_KEY='lastfm')
JOB_OPTIONS = {'LLM_JOB_BUDGET_USD', 'LLM_EXECUTION_MODE', 'CLASSIFY_INCLUDE_LYRICS'}


@dataclass(repr=False)
class DeveloperProfile:
    settings: object
    keys: dict
    job_defaults: dict
    ignored: list
    source: str

    def __repr__(self):
        return '<DeveloperProfile secrets hidden>'


def parse_env(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024:
        raise ValueError('개발 프로필 크기 한도는 128KiB입니다.')
    values = {}
    for number, line in enumerate(path.read_text('utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*', line)
        if not match:
            raise ValueError(f'개발 프로필 {number}행의 KEY=VALUE 형식을 확인하세요.')
        key, value = match.groups()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'개발 프로필 {number}행 따옴표를 확인하세요.')
            value = value[1:-1]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        # Literal values only: no interpolation, eval, shell expansion or execution.
        values[key] = value
    return values


def boolean(value):
    if value.casefold() not in {'true', 'false'}:
        raise ValueError('논리 설정은 true 또는 false로 입력하세요.')
    return value.casefold() == 'true'


def load_profile(path, base, environment=None):
    path = Path(path).resolve()
    values = parse_env(path)
    environment = os.environ if environment is None else environment
    known = MAPPING.keys() | KEYS.keys() | JOB_OPTIONS
    values.update({key: environment[key] for key in known if key in environment})
    changes = {}
    for key, (field, converter) in MAPPING.items():
        if key in values and values[key]:
            try:
                changes[field] = boolean(values[key]) if converter is bool else converter(values[key])
            except (TypeError, ValueError):
                raise ValueError(f'{key} 설정 형식을 확인하세요.') from None
    if changes.get('music_root'):
        root = Path(changes['music_root'])
        changes['music_root'] = str((path.parent / root).resolve() if not root.is_absolute() else root.resolve())
    settings = replace(base, **changes)
    settings.validate()
    keys = {provider: values[key] for key, provider in KEYS.items() if values.get(key)}
    if any(any(ord(char) < 33 or ord(char) > 126 for char in value) for value in keys.values()):
        raise ValueError('개발 프로필 API 키의 공백·줄바꿈·문자 형식을 확인하세요.')
    defaults = {}
    if values.get('LLM_JOB_BUDGET_USD'):
        budget_micro(values['LLM_JOB_BUDGET_USD'])
        defaults['budget'] = values['LLM_JOB_BUDGET_USD']
    if values.get('LLM_EXECUTION_MODE'):
        if values['LLM_EXECUTION_MODE'] not in {'sync', 'batch'}:
            raise ValueError('LLM_EXECUTION_MODE는 sync 또는 batch입니다.')
        defaults['execution'] = values['LLM_EXECUTION_MODE']
    if values.get('CLASSIFY_INCLUDE_LYRICS'):
        defaults['include_lyrics'] = boolean(values['CLASSIFY_INCLUDE_LYRICS'])
    return DeveloperProfile(settings, keys, defaults, [key for key, value in values.items() if key not in known and value], str(path))
