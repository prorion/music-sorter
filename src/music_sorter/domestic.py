"""Bounded public music-page lookup and field-level corroboration, without an LLM."""
import hashlib
import json
import re
import threading
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .database import encode, normalized, now

SITES = {'melon': '멜론', 'bugs': '벅스', 'genie': '지니'}
HOSTS = {'www.melon.com': 'melon', 'music.bugs.co.kr': 'bugs', 'www.genie.co.kr': 'genie'}
STATES = {'corroborated': '여러 출처에서 일치', 'conflict': '출처마다 정보가 다름',
          'insufficient': '근거 부족', 'not_found': '결과 없음', 'failed': '조회 오류', 'skipped': '사용 안 함'}
_lock = threading.Lock()
_next = {}


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def find(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                    yield child
                yield from child.find(tag, cls)

    def text(self, raw=False):
        if not raw and (self.tag in {'script', 'style'} or 'none' in self.attrs.get('class', '').split()):
            return ''
        return ' '.join(str(c) if isinstance(c, str) else c.text(raw) for c in self.children)


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.count = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 50000 or len(self.stack) > 100:
            raise ValueError('페이지 구조가 너무 복잡합니다.')
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def clean(text, limit=512):
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:limit]


def first(node, tag=None, cls=None):
    return next(node.find(tag, cls), Node())


def canonical_url(url, search=False):
    if not isinstance(url, str) or len(url) > 2500 or any(ord(c) < 32 or c.isspace() for c in url):
        raise ValueError('출처 주소의 길이·공백·제어 문자를 확인하세요.')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in HOSTS or parsed.fragment:
        raise ValueError('멜론·벅스·지니의 HTTPS 곡 상세 주소만 지원합니다.')
    params = parse_qs(parsed.query, keep_blank_values=True)
    site = HOSTS[parsed.netloc]
    if site == 'melon' and parsed.path == '/song/detail.htm' and set(params) == {'songId'}:
        identity = params['songId']
        if len(identity) == 1 and re.fullmatch(r'\d{1,12}', identity[0]):
            return site, 'https://www.melon.com/song/detail.htm?songId=' + identity[0]
    if site == 'bugs' and re.fullmatch(r'/(track|album)/\d{1,12}', parsed.path) and set(params) <= {'wl_ref'}:
        return site, 'https://music.bugs.co.kr' + parsed.path
    if site == 'genie' and parsed.path == '/detail/songInfo' and set(params) == {'xgnm'}:
        identity = params['xgnm']
        if len(identity) == 1 and re.fullmatch(r'\d{1,12}', identity[0]):
            return site, 'https://www.genie.co.kr/detail/songInfo?xgnm=' + identity[0]
    if search and ((site == 'melon' and parsed.path == '/search/song/index.htm') or
                   (site == 'bugs' and parsed.path == '/search/track')) and set(params) == {'q'} and len(params['q']) == 1:
        return site, url
    if search and site == 'genie' and parsed.path == '/search/searchMain' and set(params) == {'query'} and len(params['query']) == 1:
        return site, url
    raise ValueError('지원하는 곡 상세 주소 형식을 확인하세요.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch_page(url):
    site, url = canonical_url(url, search=True)
    headers = {'User-Agent': f'music-sorter/{__version__} (public music metadata lookup)', 'Accept': 'text/html'}
    try:
        with _lock:
            time.sleep(max(0, _next.get(site, 0) - time.monotonic()))
            _next[site] = time.monotonic() + 2.0
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=12) as response:
            if 'html' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('음악 정보 HTML 페이지가 아닙니다.')
            raw = response.read(2 * 1024 * 1024 + 1)
            charset = response.headers.get_content_charset() or 'utf-8'
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('페이지 크기가 조회 한도를 넘었습니다.')
        return raw.decode(charset)
    except HTTPError as error:
        raise ValueError(f'{SITES[site]} 조회 보류 (HTTP {error.code}). 출처 링크에서 확인하세요.') from None
    except (URLError, OSError, UnicodeError, LookupError):
        raise ValueError(f'{SITES[site]} 페이지를 읽지 못했습니다. 네트워크·페이지 변경을 확인하세요.') from None


def search_candidates(site, text):
    root = Document(text).root
    candidates = []
    for row in root.find('tr'):
        if site == 'melon':
            anchors = list(row.find('a'))
            detail = next((a for a in anchors if 'goSongDetail(' in a.attrs.get('href', '')), None)
            if detail is None:
                continue
            identity = re.search(r"goSongDetail\(['\"](\d+)['\"]\)", detail.attrs['href'])
            title = first(row, 'a', 'fc_gray')
            if not title.text().strip():
                title = next((n for n in row.find('span') if n.attrs.get('title') and clean(n.text()) == n.attrs['title']), Node())
            artist = first(row, cls='wrapArtistName')
            names = [clean(a.text()) for a in artist.find('a')]
            album = next((a for a in anchors if 'goAlbumDetail(' in a.attrs.get('href', '')), Node())
            if not identity:
                continue
            url = 'https://www.melon.com/song/detail.htm?songId=' + identity[1]
            artist_text = ' / '.join(dict.fromkeys(names))
        elif site == 'bugs':
            if not row.attrs.get('trackid'):
                continue
            url = 'https://music.bugs.co.kr/track/' + row.attrs['trackid']
            title = first(row, cls='title')
            artist_text = clean(first(row, cls='artist').text())
            album = first(row, cls='album')
        else:
            detail = next((a for a in row.find('a') if 'fnViewSongInfo(' in a.attrs.get('onclick', '')), None)
            if detail is None:
                continue
            identity = re.search(r"fnViewSongInfo\(['\"](\d+)['\"]\)", detail.attrs['onclick'])
            if not identity:
                continue
            url = 'https://www.genie.co.kr/detail/songInfo?xgnm=' + identity[1]
            title = first(row, 'a', 'title')
            artist_text = clean(first(row, cls='artist').text())
            album = first(row, cls='albumtitle')
        title_text = clean(title.attrs.get('title') or title.text()) if site == 'genie' else clean(title.text())
        candidates.append(dict(site=site, url=url, title=title_text, artist=artist_text, album=clean(album.text())))
        if len(candidates) == 25:
            break
    if not candidates and not any(marker in text for marker in ('검색 결과', '검색결과', 'serch_totcnt')):
        raise ValueError('검색 페이지 구조가 바뀌었습니다. 직접 출처 주소를 추가해 확인하세요.')
    return candidates


def genre_set(text):
    return sorted(set(clean(value, 100) for value in re.split(r'[,，·]', text) if clean(value)))


def parse_page(url, text):
    site, url = canonical_url(url)
    root = Document(text).root
    row = dict(site=site, url=url, title='', artist='', album='', genres=[], genre_scope='track', release_date='', duration=None)
    if site == 'melon':
        section = first(root, cls='section_info')
        row.update(title=clean(first(section, cls='song_name').text()), artist=clean(first(section, cls='artist_name').text()))
        meta = first(section, cls='meta')
        fields = dict(zip([clean(n.text()) for n in meta.find('dt')], [clean(n.text()) for n in meta.find('dd')]))
        row.update(album=fields.get('앨범', ''), genres=genre_set(fields.get('장르', '')),
                   raw_genre=fields.get('장르', ''), release_date=fields.get('발매일', '').replace('.', '-'))
    elif site == 'bugs' and '/track/' in url:
        recordings = []
        for script in root.find('script'):
            if script.attrs.get('type') != 'application/ld+json':
                continue
            try:
                obj = json.loads(script.text(raw=True))
                if isinstance(obj, dict) and obj.get('@type') == 'MusicRecording':
                    recordings.append(obj)
            except (ValueError, TypeError):
                continue
        if len(recordings) != 1:
            raise ValueError('벅스 곡 정보 형식이 바뀌었거나 공식 곡 상세가 아닙니다.')
        obj = recordings[0]
        artist, album = obj.get('byArtist', {}), obj.get('inAlbum', {})
        if not isinstance(artist, dict) or not isinstance(album, dict):
            raise ValueError('벅스 아티스트·앨범 정보를 확인할 수 없습니다.')
        row.update(title=clean(obj.get('name')), artist=clean(artist.get('name')), album=clean(album.get('name')),
                   release_date=clean(obj.get('datePublished'), 30))
        match = re.fullmatch(r'PT(?:(\d+)M)?(?:(\d+)S)?', str(obj.get('duration', '')))
        if match:
            row['duration'] = int(match[1] or 0) * 60 + int(match[2] or 0)
        if album.get('url'):
            _, row['album_url'] = canonical_url(album['url'])
    elif site == 'bugs':
        fields = {}
        section = first(root, cls='summaryInfo')
        for tr in first(section, 'table', 'info').find('tr'):
            fields[clean(first(tr, 'th').text())] = clean(first(tr, 'td').text())
        row.update(genres=genre_set(fields.get('장르', '')), raw_genre=fields.get('장르', ''),
                   genre_scope='album', release_date=fields.get('발매일', '').replace('.', '-'))
        table = first(root, 'table', 'trackList')
        ids = [tr.attrs['trackid'] for tr in table.find('tr') if tr.attrs.get('trackid')]
        row['album_track_ids'] = ids
    else:
        info = first(root, cls='info-zone')
        row['title'] = clean(first(info, cls='name').text())
        fields = {}
        for li in info.find('li'):
            label = first(li, 'span', 'attr')
            img = first(label, 'img')
            name = '아티스트' if 'txt_5.png' in img.attrs.get('src', '') else '앨범명' if 'txt_6.png' in img.attrs.get('src', '') else img.attrs.get('alt', '')
            fields[clean(name)] = clean(first(li, cls='value').text())
        raw_genre = fields.get('장르', '')
        genre = raw_genre.split(' / ', 1)[-1] if raw_genre.startswith(('가요 / ', 'POP / ')) else raw_genre
        row.update(artist=fields.get('아티스트', ''), album=fields.get('앨범명', ''),
                   genres=genre_set(genre), raw_genre=raw_genre, release_date=fields.get('발매일', '').replace('.', '-'))
        match = re.search(r'(\d+):(\d{2})', fields.get('재생시간', ''))
        if match:
            row['duration'] = int(match[1]) * 60 + int(match[2])
    if '/album/' not in url and (not row['title'] or not row['artist']):
        raise ValueError(f'{SITES[site]} 곡 정보 형식이 바뀌었습니다. 출처 링크에서 확인하세요.')
    return row


def artist_aliases(value):
    value = clean(value)
    aliases = {normalized(value)}
    match = re.fullmatch(r'([^()]+)\s*\(([^()]+)\)', value)
    if match and not re.search(r'feat|with|[,/]', match[2], re.I):
        aliases.update(normalized(part) for part in match.groups())
    return aliases


def matches(track, row, tolerance):
    from .external import version
    if normalized(track['title']) != normalized(row['title']) or not (artist_aliases(track['artist']) & artist_aliases(row['artist'])):
        return False, '제목 또는 아티스트가 다름'
    if version(row['title']) != track.get('version', ''):
        return False, '곡 버전이 다름'
    if track.get('album') and row.get('album') and normalized(track['album']) != normalized(row['album']):
        return False, '파일의 앨범과 다름'
    if track.get('duration') is not None and row.get('duration') is not None and abs(track['duration'] - row['duration']) > tolerance:
        return False, '재생 시간이 허용 범위를 넘음'
    return True, '제목·아티스트 일치, 확인 가능한 버전·앨범·길이 모순 없음 (동일 녹음 확정 아님)'


def compare(track, sources, failures=()):
    valid = [s for s in sources if s.get('accepted')]
    by_site = {}
    for row in valid:
        if row.get('genres') and row['genre_scope'] in {'track', 'single_album'}:
            by_site.setdefault(row['site'], set()).add(tuple(row['genres']))
    values = {value for group in by_site.values() for value in group}
    state = 'conflict' if len(values) > 1 else 'corroborated' if len(by_site) >= 2 else 'insufficient' if valid else 'failed' if failures else 'not_found'
    agreed = list(next(iter(values))) if state == 'corroborated' else []
    result = dict(state=state, reason=STATES[state], sources=sources, failures=list(failures),
                  source_count=len({s['site'] for s in valid}), genre_source_count=len(by_site), agreed_genres=agreed, evidence=[])
    if valid:
        result['evidence'] = [dict(service='domestic', title=track['title'], artist=track['artist'],
                                   tags=', '.join(agreed), verification=state,
                                   source_urls=' | '.join(dict.fromkeys(s['url'] for s in valid))[:1500],
                                   match_reason=STATES[state] + ' · 사이트 수는 독립 원자료 수나 정답 확률이 아님')]
    return result


class DomesticLookup:
    def __init__(self, library, settings, fetch=None, control=None):
        self.library, self.settings, self.fetch, self.control = library, settings, fetch or fetch_page, control

    def page(self, url, refresh=False):
        if self.control:
            self.control.checkpoint()
        _, url = canonical_url(url, search=True)
        key = hashlib.sha256(('domestic-page-v1:' + url).encode()).hexdigest()
        with self.library.connection() as db:
            cached = db.execute('SELECT result,created_at FROM external_cache WHERE key=?', (key,)).fetchone()
        if cached and not refresh:
            result = json.loads(cached['result'])
            return result['value'], cached['created_at']
        if self.control:
            self.control.checkpoint()
        text = self.fetch(url)
        if not isinstance(text, str) or len(text.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('페이지 응답 크기·형식을 확인하세요.')
        value = search_candidates(HOSTS[urlsplit(url).netloc], text) if '/search/' in url else parse_page(url, text)
        stamp = now()
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)',
                       (key, 'domestic_page', encode(dict(url=url)), encode(dict(value=value)), stamp))
        return value, stamp

    def source(self, track, url, refresh=False):
        row, stamp = self.page(url, refresh)
        row = {**row, 'queried_at': stamp}
        if '/album/' in row['url']:
            raise ValueError('곡 상세 주소를 추가하세요. 앨범 전체 주소는 지원하지 않습니다.')
        row['accepted'], row['match_reason'] = matches(track, row, self.settings.duplicate_tolerance_seconds)
        if row['accepted'] and row.get('album_url'):
            try:
                album, album_stamp = self.page(row['album_url'], refresh)
            except ValueError as error:
                row['album_error'] = str(error)
                return row
            row['album_genres'] = album['genres']
            row['album_raw_genre'] = album.get('raw_genre', '')
            row['album_queried_at'] = album_stamp
            identity = row['url'].rsplit('/', 1)[-1]
            if album.get('album_track_ids') == [identity]:
                row.update(genres=album['genres'], genre_scope='single_album')
            else:
                row['genre_scope'] = 'album_reference'
        return row

    def lookup(self, track, refresh=False, extra_urls=()):
        from .external import input_for, query_key
        if not self.settings.domestic_enabled:
            return dict(state='skipped', reason='국내 검색 사용 안 함', evidence=[])
        if not track.get('title') or not track.get('artist'):
            return dict(state='skipped', reason='제목·아티스트 정보 부족', evidence=[])
        urls = list(dict.fromkeys(canonical_url(u)[1] for u in extra_urls))
        if len(urls) > 3 or any('/album/' in url for url in urls):
            raise ValueError('추가 곡 출처는 최대 3개입니다.')
        sources, failures = [], []
        query = clean(track['artist'] + ' ' + track['title'], 500)
        def collect(site, search, parameter='q'):
            try:
                if self.control:
                    self.control.report(f'{SITES[site]} 검색 중…')
                candidates, _ = self.page(search + urlencode({parameter: query}), refresh)
                # First filter search rows, then verify the real detail page. Ranking is never a match rule.
                candidates = [c for c in candidates if matches(track, c, self.settings.duplicate_tolerance_seconds)[0]]
                if len(candidates) > 5:
                    failures.append(dict(site=site, reason='같은 이름의 후보가 5개를 넘습니다. 상세 주소를 추가해 검토하세요.'))
                    return
                for candidate in candidates:
                    try:
                        if self.control:
                            self.control.report(f'{SITES[site]} 곡 상세 확인 중…')
                        sources.append(self.source(track, candidate['url'], refresh))
                    except ValueError as error:
                        failures.append(dict(site=site, reason=str(error)))
            except ValueError as error:
                failures.append(dict(site=site, reason=str(error)))
        collect('melon', 'https://www.melon.com/search/song/index.htm?')
        collect('bugs', 'https://music.bugs.co.kr/search/track?')
        if compare(track, sources, failures)['state'] != 'corroborated':
            collect('genie', 'https://www.genie.co.kr/search/searchMain?', 'query')
        for url in urls:
            if url in {s['url'] for s in sources}:
                continue
            try:
                sources.append(self.source(track, url, refresh))
            except ValueError as error:
                failures.append(dict(site=canonical_url(url)[0], reason=str(error)))
        if self.control:
            self.control.report(f'출처 정보 대조 중 · 확인한 상세 {len(sources)}개')
        result = compare(track, sources, failures)
        result['extra_urls'] = urls
        stamp = now()
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO external_cache VALUES (?,?,?,?,?)',
                       (query_key('domestic', track, self.settings.duplicate_tolerance_seconds), 'domestic', encode(input_for(track)), encode(result), stamp))
        return {**result, 'queried_at': stamp, 'cached': False}
