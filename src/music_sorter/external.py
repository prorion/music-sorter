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

_rate_lock = threading.Lock()
_next_mb = 0.0


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def input_for(track):
    return {key: track.get(key) for key in ('title', 'artist', 'album', 'version', 'duration', 'hash')}


def query_key(service, track):
    return hashlib.sha256(encode({'service': service, 'input': input_for(track), 'version': 1}).encode('utf-8')).hexdigest()


def version(text):
    return '|'.join(sorted(set(normalized(m.group(1)) for m in VERSION_PATTERN.finditer(text))))


def _tags(items):
    if not isinstance(items, list):
        return []
    return [str(item['name'])[:100] for item in items[:30] if isinstance(item, dict) and isinstance(item.get('name'), str)]


def match_musicbrainz(track, payload):
    rows = payload.get('recordings', [])
    if not isinstance(rows, list):
        raise ValueError('외부 곡 응답 형식 오류')
    candidates = []
    for row in rows[:25]:
        if not isinstance(row, dict):
            continue
        credits = row.get('artist-credit') or []
        artist = ''.join(str(item.get('name') or (item.get('artist') or {}).get('name', '')) + str(item.get('joinphrase', ''))
                         for item in credits if isinstance(item, dict))
        title = str(row.get('title', ''))
        albums = [str(item.get('title', '')) for item in (row.get('releases') or []) if isinstance(item, dict)]
        exact = bool(track['artist'] and track['title']) and normalized(title) == normalized(track['title']) and normalized(artist) == normalized(track['artist'])
        exact &= version(title + ' ' + str(row.get('disambiguation', ''))) == track.get('version', '')
        length = row.get('length')
        if track.get('duration') is not None and length:
            try:
                exact &= abs(float(length) / 1000 - track['duration']) <= 3
            except (TypeError, ValueError):
                exact = False
        if track['album'] and albums:
            exact &= normalized(track['album']) in {normalized(item) for item in albums}
        candidate = dict(recording_id=str(row.get('id', ''))[:100], title=title[:512], artist=artist[:512],
                         albums=albums[:10], tags=_tags(row.get('tags', [])), exact=bool(exact))
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', candidate['recording_id']):
            candidates.append(candidate)
    matched = {c['recording_id']: c for c in candidates if c['exact']}
    complete = int(payload.get('count', len(rows))) <= len(rows)
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
        raise ValueError('Last.fm 요청 실패. 키·권한·요청 제한을 확인하세요.')
    row = payload.get('track')
    if not isinstance(row, dict):
        return dict(state='not_found', evidence=[])
    artist = row.get('artist') or {}
    artist = artist.get('name', '') if isinstance(artist, dict) else str(artist)
    title = str(row.get('name', ''))
    exact = normalized(title) == normalized(track['title']) and normalized(artist) == normalized(track['artist'])
    exact &= version(title) == track.get('version', '')
    album = (row.get('album') or {}).get('title', '')
    if track['album'] and album:
        exact &= normalized(album) == normalized(track['album'])
    if not exact or not track['artist']:
        return dict(state='ambiguous', evidence=[])
    tags = _tags((row.get('toptags') or {}).get('tag', []))
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
            headers['User-Agent'] = f'music-sorter/0.4 ({self.settings.musicbrainz_contact})'
            url = 'https://musicbrainz.org/ws/2/recording/?' + urlencode(params)
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
        except (HTTPError, URLError, OSError, ValueError):
            raise ValueError('외부 음악 조회 실패. 네트워크·키·권한·응답을 확인하세요.') from None

    def cached(self, track, service):
        with self.library.connection() as db:
            row = db.execute('SELECT result,created_at FROM external_cache WHERE key=?', (query_key(service, track),)).fetchone()
        return {**json.loads(row['result']), 'queried_at': row['created_at'], 'cached': True} if row else None

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
            params = dict(query=f'recording:"{escape(track["title"])}" AND artist:"{escape(track["artist"])}"', fmt='json', limit=25)
            result = match_musicbrainz(track, self.fetch(service, params))
        elif service == 'lastfm':
            if not self.settings.lastfm_enabled or not self.lastfm_key:
                return dict(state='skipped', reason='Last.fm 사용 또는 키 미설정', evidence=[])
            result = match_lastfm(track, self.fetch(service, dict(method='track.getInfo', artist=track['artist'], track=track['title'], autocorrect=0, format='json')))
        else:
            raise ValueError('지원하지 않는 외부 음악 서비스입니다.')
        stamp = now()
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)',
                       (query_key(service, track), service, encode(input_for(track)), encode(result), stamp))
        return {**result, 'queried_at': stamp, 'cached': False}

    def evidence(self, track):
        services = [('musicbrainz', self.settings.musicbrainz_enabled), ('lastfm', self.settings.lastfm_enabled)]
        return [item for service, enabled in services if enabled for item in (self.cached(track, service) or {}).get('evidence', [])]
