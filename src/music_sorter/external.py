"""Conservative recording evidence: fixed hosts, bounded JSON and persistent caches."""
import hashlib
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .database import encode, normalized, now
from .scanner import VERSION_PATTERN
from .tag_io import digest
from pathlib import Path
from . import __version__

_rate_lock = threading.Lock()
_next_mb = 0.0


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def input_for(track):
    values = {key: track.get(key) for key in ('title', 'artist', 'album', 'version', 'duration', 'hash')}
    values.update({key: track.get('metadata_json', {}).get(key, []) for key in ('recording_ids', 'isrcs')})
    return values


def query_key(service, track, tolerance=3):
    return hashlib.sha256(encode({'service': service, 'input': input_for(track), 'tolerance': tolerance, 'version': 2}).encode('utf-8')).hexdigest()


def version(text):
    return '|'.join(sorted(set(normalized(m.group(1)) for m in VERSION_PATTERN.finditer(text))))


def _tags(items):
    if not isinstance(items, list):
        return []
    return [str(item['name'])[:100] for item in items[:30] if isinstance(item, dict) and isinstance(item.get('name'), str)]


def match_musicbrainz(track, payload, tolerance=3):
    rows = payload.get('recordings', [])
    if not isinstance(rows, list):
        raise ValueError('외부 곡 응답 형식 오류')
    total = payload.get('count', len(rows))
    if type(total) is not int or total < 0:
        raise ValueError('외부 곡 응답 형식 오류')
    candidates = []
    for row in rows[:25]:
        if not isinstance(row, dict):
            continue
        credits = row.get('artist-credit') or []
        if not isinstance(credits, list) or not isinstance(row.get('releases', []), list):
            raise ValueError('외부 곡 응답 형식 오류')
        artist = ''.join(str(item.get('name') or (item.get('artist') or {}).get('name', '')) + str(item.get('joinphrase', ''))
                         for item in credits if isinstance(item, dict))
        title = str(row.get('title', ''))
        albums = [str(item.get('title', '')) for item in (row.get('releases') or []) if isinstance(item, dict)]
        exact = bool(track['artist'] and track['title']) and normalized(title) == normalized(track['title']) and normalized(artist) == normalized(track['artist'])
        exact &= version(title + ' ' + str(row.get('disambiguation', ''))) == track.get('version', '')
        length = row.get('length')
        if track.get('duration') is not None and length:
            try:
                exact &= abs(float(length) / 1000 - track['duration']) <= tolerance
            except (TypeError, ValueError):
                exact = False
        if track['album'] and albums:
            exact &= normalized(track['album']) in {normalized(item) for item in albums}
        candidate = dict(recording_id=str(row.get('id', ''))[:100], title=title[:512], artist=artist[:512],
                         albums=albums[:10], tags=_tags(row.get('tags', [])), exact=bool(exact),
                         length_ms=length, disambiguation=str(row.get('disambiguation', ''))[:512])
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', candidate['recording_id']):
            candidates.append(candidate)
    matched = {c['recording_id']: c for c in candidates if c['exact']}
    complete = total <= min(len(rows), 25)
    result = dict(state='matched' if len(matched) == 1 and complete else 'ambiguous' if candidates else 'not_found',
                  candidates=candidates, evidence=[])
    if result['state'] == 'matched':
        row = next(iter(matched.values()))
        result['evidence'] = [dict(service='musicbrainz', recording_id=row['recording_id'], title=row['title'], artist=row['artist'],
                                   album=' / '.join(row['albums']), tags=', '.join(row['tags']),
                                   match_reason='유일한 제목·아티스트·버전 일치, 앨범·길이 모순 없음')]
    return result


def match_lastfm(track, payload):
    if 'error' in payload:
        if payload['error'] == 6:
            return dict(state='not_found', evidence=[])
        raise ValueError('Last.fm 요청 실패. 키·권한·요청 제한을 확인하세요.')
    row = payload.get('track')
    if not isinstance(row, dict):
        return dict(state='not_found', evidence=[])
    artist = row.get('artist') or {}
    artist = artist.get('name', '') if isinstance(artist, dict) else str(artist)
    title = str(row.get('name', ''))
    exact = normalized(title) == normalized(track['title']) and normalized(artist) == normalized(track['artist'])
    exact &= version(title) == track.get('version', '')
    album_row = row.get('album') or {}
    tags_row = row.get('toptags') or {}
    if not isinstance(album_row, dict) or not isinstance(tags_row, dict):
        raise ValueError('Last.fm 곡 응답 형식 오류')
    album = album_row.get('title', '')
    if track['album'] and album:
        exact &= normalized(album) == normalized(track['album'])
    if not exact or not track['artist']:
        return dict(state='ambiguous', evidence=[])
    tags = _tags(tags_row.get('tag', []))
    return dict(state='reference', evidence=[dict(service='lastfm', title=title[:512], artist=artist[:512], album=str(album)[:512],
                                                  tags=', '.join(tags), match_reason='이름·버전 일치 참고 태그. 녹음 식별 확정 출처 아님')])


class ExternalLookup:
    def __init__(self, library, settings, lastfm_key='', fetch=None):
        self.library, self.settings, self.lastfm_key = library, settings, lastfm_key
        self.fetch = fetch or self._fetch

    def _fetch(self, service, params):
        global _next_mb
        headers = {'Accept': 'application/json'}
        if service == 'musicbrainz':
            headers['User-Agent'] = f'music-sorter/{__version__} ({self.settings.musicbrainz_contact})'
            params = dict(params)
            entity, identity = params.pop('_entity', 'recording'), params.pop('_id', '')
            if entity not in {'recording', 'isrc'} or (identity and not re.fullmatch(r'[A-Za-z0-9-]{12,36}', identity)):
                raise ValueError('외부 식별 ID 형식 오류')
            url = f'https://musicbrainz.org/ws/2/{entity}/{identity}' + '?' + urlencode(params)
        else:
            params = dict(params, api_key=self.lastfm_key)
            url = 'https://ws.audioscrobbler.com/2.0/?' + urlencode(params)
        try:
            if service == 'musicbrainz':
                with _rate_lock:
                    time.sleep(max(0, _next_mb - time.monotonic()))
                    _next_mb = time.monotonic() + 1.05
                    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=12) as response:
                        raw = response.read(2 * 1024 * 1024 + 1)
            else:
                with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=12) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except HTTPError as error:
            if service == 'musicbrainz' and error.code == 404:
                return dict(recordings=[], count=0)
            raise ValueError('외부 음악 조회 실패. 네트워크·키·권한·응답을 확인하세요.') from None
        except (URLError, OSError, ValueError):
            raise ValueError('외부 음악 조회 실패. 네트워크·키·권한·응답을 확인하세요.') from None

    def cached(self, track, service):
        with self.library.connection() as db:
            row = db.execute('SELECT result,created_at FROM external_cache WHERE key=?', (query_key(service, track, self.settings.duplicate_tolerance_seconds),)).fetchone()
        return {**json.loads(row['result']), 'queried_at': row['created_at'], 'cached': True} if row else None

    def record_failure(self, track, service, reason):
        if service not in {'musicbrainz', 'lastfm'}:
            raise ValueError('지원하지 않는 외부 음악 서비스입니다.')
        # Caller passes only the adapter's safe messages, never raw provider exceptions.
        result = dict(state='failed', reason=reason[:600], evidence=[])
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)',
                       (query_key(service, track, self.settings.duplicate_tolerance_seconds), service, encode(input_for(track)), encode(result), now()))
        return result

    def lookup(self, track, service, refresh=False):
        cached = self.cached(track, service)
        if cached and not refresh:
            return cached
        if not track['title'] or not track['artist']:
            return dict(state='skipped', reason='제목·아티스트 정보 부족', evidence=[])
        if service == 'musicbrainz':
            if not self.settings.musicbrainz_enabled or not self.settings.musicbrainz_contact:
                return dict(state='skipped', reason='MusicBrainz 사용 또는 연락처 미설정', evidence=[])
            escape = lambda value: re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', value)
            identifiers = input_for(track)
            mbids, isrcs = identifiers['recording_ids'], identifiers['isrcs']
            if len(mbids) > 1 or (not mbids and len(isrcs) > 1):
                result = dict(state='ambiguous', reason='기존 녹음 ID가 여러 개입니다. 메타데이터를 검토하세요.', evidence=[], candidates=[])
            else:
                if mbids or isrcs:
                    identity, entity = (mbids[0], 'recording') if mbids else (isrcs[0], 'isrc')
                    pattern = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}' if entity == 'recording' else r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}'
                    if not re.fullmatch(pattern, identity):
                        raise ValueError('기존 녹음 ID 형식 오류')
                    params = dict(_entity=entity, _id=identity, inc='artist-credits+releases+tags', fmt='json')
                    payload = self.fetch(service, params)
                    if entity == 'recording' and payload.get('id'):
                        payload = dict(recordings=[payload], count=1)
                else:
                    params = dict(query=f'recording:"{escape(track["title"])}" AND artist:"{escape(track["artist"])}"', fmt='json', limit=25)
                    payload = self.fetch(service, params)
                result = match_musicbrainz(track, payload, self.settings.duplicate_tolerance_seconds)
                result['lookup_method'] = 'recording_id' if mbids else 'isrc' if isrcs else 'title_artist'
        elif service == 'lastfm':
            if not self.settings.lastfm_enabled or not self.lastfm_key:
                return dict(state='skipped', reason='Last.fm 사용 또는 키 미설정', evidence=[])
            result = match_lastfm(track, self.fetch(service, dict(method='track.getInfo', artist=track['artist'], track=track['title'], autocorrect=0, format='json')))
            artist = self.artist_reference(track['artist'], refresh)
            result['artist_reference'] = artist
            result['evidence'].extend(artist.get('evidence', []))
        else:
            raise ValueError('지원하지 않는 외부 음악 서비스입니다.')
        stamp = now()
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)',
                       (query_key(service, track, self.settings.duplicate_tolerance_seconds), service, encode(input_for(track)), encode(result), stamp))
        return {**result, 'queried_at': stamp, 'cached': False}

    def artist_reference(self, artist, refresh=False):
        key = hashlib.sha256(encode(dict(service='lastfm_artist', artist=normalized(artist), version=1)).encode()).hexdigest()
        with self.library.connection() as db:
            cached = db.execute('SELECT result FROM external_cache WHERE key=?', (key,)).fetchone()
        if cached and not refresh:
            return json.loads(cached[0])
        payload = self.fetch('lastfm', dict(method='artist.getTopTags', artist=artist, autocorrect=0, format='json'))
        if 'error' in payload:
            if payload['error'] != 6:
                raise ValueError('Last.fm 아티스트 태그 조회 실패. 키·권한을 확인하세요.')
            payload = {}
        row = payload.get('toptags') or {}
        if not isinstance(row, dict) or not isinstance(row.get('@attr', {}), dict):
            raise ValueError('Last.fm 아티스트 응답 형식 오류')
        actual = row.get('@attr', {}).get('artist', '')
        result = dict(state='reference' if actual and normalized(actual) == normalized(artist) else 'not_found', evidence=[])
        if result['state'] == 'reference':
            result['evidence'] = [dict(service='lastfm_artist', artist=str(actual)[:512], tags=', '.join(_tags(row.get('tag', []))),
                                       match_reason='아티스트 이름 일치 참고 태그. 개별 곡의 장르·녹음 정답 아님')]
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)', (key, 'lastfm_artist', encode(dict(artist=artist)), encode(result), now()))
        return result

    def choose_candidate(self, track_id, recording_id, expected, revision, reason):
        if not reason.strip() or len(reason) > 600:
            raise ValueError('후보 선택 근거를 1~600자로 입력하세요.')
        track = self.library.track(track_id)
        cached = self.cached(track, 'musicbrainz')
        if track['revision'] != revision or track['file_state'] != 'ready' or not cached or cached != expected:
            raise ValueError('곡·판정·조회 결과가 바뀌었습니다. 다시 열어 검토하세요.')
        candidate = next((row for row in cached.get('candidates', []) if row['recording_id'] == recording_id), None)
        if not candidate or digest(Path(track['path'])) != track['hash']:
            raise ValueError('후보가 없거나 파일 내용이 바뀌었습니다. 재스캔하세요.')
        key = query_key('musicbrainz', track, self.settings.duplicate_tolerance_seconds)
        result = {key: value for key, value in cached.items() if key not in {'queried_at', 'cached'}}
        result.update(state='matched', manual_selection=dict(recording_id=recording_id, reason=reason.strip(), selected_at=now()),
                      evidence=[dict(service='musicbrainz', recording_id=recording_id, title=candidate['title'], artist=candidate['artist'],
                                     album=' / '.join(candidate['albums']), tags=', '.join(candidate['tags']), match_reason='사용자 후보 확인: ' + reason.strip())])
        with self.library.connection(write=True) as db:
            row = db.execute('SELECT result,created_at FROM external_cache WHERE key=?', (key,)).fetchone()
            current = db.execute('SELECT revision,hash,file_state,path FROM tracks WHERE id=?', (track_id,)).fetchone()
            if not row or not current or row['created_at'] != cached['queried_at'] or json.loads(row['result']) != {k: v for k, v in cached.items() if k not in {'queried_at', 'cached'}} or tuple(current) != (revision, track['hash'], 'ready', track['path']):
                raise ValueError('검토 중 데이터가 바뀌었습니다. 다시 확인하세요.')
            db.execute('UPDATE external_cache SET result=?,created_at=? WHERE key=?', (encode(result), now(), key))
        return result

    def evidence(self, track):
        services = [('musicbrainz', self.settings.musicbrainz_enabled), ('lastfm', self.settings.lastfm_enabled)]
        return [item for service, enabled in services if enabled for item in (self.cached(track, service) or {}).get('evidence', [])]
