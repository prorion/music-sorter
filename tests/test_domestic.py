import json

import pytest

from music_sorter.domestic import (DomesticLookup, canonical_url, compare, matches, parse_page, search_candidates)
from music_sorter.external import ExternalLookup
from music_sorter.settings import Settings

MELON = 'https://www.melon.com/song/detail.htm?songId=1'
BUGS = 'https://music.bugs.co.kr/track/2'
ALBUM = 'https://music.bugs.co.kr/album/3'
GENIE = 'https://www.genie.co.kr/detail/songInfo?xgnm=4'
TRACK = dict(title='밤편지', artist='아이유', album='', version='', duration=253, hash='fixture')


def melon(genre='발라드', title='밤편지'):
    return f'<div class="section_info"><div class="song_name"><strong class="none">곡명</strong>{title}</div><a class="artist_name">아이유</a><div class="meta"><dl><dt>앨범</dt><dd>밤편지</dd><dt>발매일</dt><dd>2017.03.24</dd><dt>장르</dt><dd>{genre}</dd></dl></div></div><div id="lyric">PRIVATE_LYRICS</div><div>이별 플레이리스트 태그</div>'


def bugs():
    obj = {'@type': 'MusicRecording', 'name': '밤편지', 'byArtist': {'name': '아이유(IU)'},
           'inAlbum': {'name': '밤편지', 'url': ALBUM}, 'duration': 'PT4M13S', 'datePublished': '2017-03-24'}
    return '<script type="application/ld+json">' + json.dumps(obj) + '</script><div>PRIVATE_LYRICS</div>'


def album(single=True):
    return '<section class="summaryInfo"><table class="info"><tr><th>장르</th><td>발라드</td></tr></table></section><table class="trackList"><tr trackId="2"></tr>' + ('' if single else '<tr trackId="9"></tr>') + '</table>'


def search(site):
    if site == 'melon':
        return '<table><tr><a href="javascript:melon.link.goSongDetail(\'1\');"></a><a class="fc_gray">밤편지</a><div class="wrapArtistName"><a>아이유</a></div><a href="javascript:goAlbumDetail(\'3\')">밤편지</a></tr></table>'
    if site == 'bugs':
        return '<table><tr trackId="2"><p class="title">밤편지</p><p class="artist">아이유(IU)</p><a class="album">밤편지</a></tr></table>'
    return '<table><tr><a onclick="fnViewSongInfo(\'4\');"></a><a class="title" title="밤편지">TITLE 밤편지</a><a class="artist">아이유 (IU)</a><a class="albumtitle">밤편지</a></tr></table>'


def source(site='melon', genre='발라드', scope='track'):
    return dict(site=site, url=MELON if site == 'melon' else BUGS, title='밤편지', artist='아이유',
                album='밤편지', genres=[genre], genre_scope=scope, accepted=True)


def test_detail_parser_keeps_only_song_metadata():
    assert parse_page(MELON, melon())['title'] == '밤편지'
    assert parse_page(BUGS, bugs())['duration'] == 253
    assert 'PRIVATE_LYRICS' not in str(parse_page(MELON, melon())) + str(parse_page(BUGS, bugs()))
    assert parse_page(ALBUM, album())['album_track_ids'] == ['2']
    for site in ('melon', 'bugs', 'genie'):
        assert search_candidates(site, search(site))[0]['title'] == '밤편지'


@pytest.mark.parametrize('url', ['http://www.melon.com/song/detail.htm?songId=1',
    'https://www.melon.com.evil.test/song/detail.htm?songId=1', 'https://www.melon.com:443/song/detail.htm?songId=1',
    'https://secret@www.melon.com/song/detail.htm?songId=1', MELON + '#x', MELON + '&target=http://127.0.0.1',
    'https://music.bugs.co.kr/track/../user/2', MELON.replace('song', '\nsong'), 'file:///C:/private.txt'])
def test_untrusted_urls_cannot_escape_supported_public_endpoints(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_corroboration_is_per_site_not_candidate_or_album_votes():
    assert compare(TRACK, [source(), source()])['state'] == 'insufficient'
    assert compare(TRACK, [source(), source('bugs')])['state'] == 'corroborated'
    conflicting = compare(TRACK, [source(), source('bugs', '댄스')])
    assert conflicting['state'] == 'conflict' and conflicting['evidence'][0]['tags'] == ''
    assert compare(TRACK, [source(), source('bugs', scope='album_reference')])['state'] == 'insufficient'
    assert compare(TRACK, [source(), source('bugs'), source('bugs', '댄스')])['state'] == 'conflict'


def test_recording_version_artist_album_and_length_must_match():
    row = parse_page(BUGS, bugs())
    assert matches(TRACK, row, 3)[0]
    for patch in (dict(title='밤편지 (Live)'), dict(artist='다른 가수'), dict(duration=290)):
        assert not matches(TRACK, {**row, **patch}, 3)[0]
    assert not matches({**TRACK, 'album': 'Palette'}, row, 3)[0]


def test_lookup_cache_and_single_album_genre_and_third_source_fallback(library):
    calls = []
    fixtures = {MELON: melon(), BUGS: bugs(), ALBUM: album()}
    def fetch(url):
        calls.append(url)
        if '/search/' in url:
            return search('melon' if 'melon.com' in url else 'bugs' if 'bugs.co.kr' in url else 'genie')
        return fixtures[url]
    lookup = DomesticLookup(library, Settings(), fetch)
    result = lookup.lookup(TRACK)
    assert result['state'] == 'corroborated' and result['agreed_genres'] == ['발라드']
    assert result['sources'][1]['genre_scope'] == 'single_album'
    assert not any('genie.co.kr' in url for url in calls)
    before = len(calls)
    assert lookup.lookup(TRACK)['state'] == 'corroborated' and len(calls) == before
    assert 'PRIVATE_LYRICS' not in str(result)
    lookup.lookup(TRACK, True)
    assert len(calls) > before


def test_multi_track_album_is_reference_and_genie_is_tried(library):
    calls = []
    def fetch(url):
        calls.append(url)
        if '/search/' in url:
            return search('melon' if 'melon.com' in url else 'bugs' if 'bugs.co.kr' in url else 'genie')
        if url == MELON:
            return melon()
        if url == BUGS:
            return bugs()
        if url == ALBUM:
            return album(False)
        raise ValueError('추가 출처 조회 실패')
    result = DomesticLookup(library, Settings(), fetch).lookup(TRACK)
    assert result['state'] == 'insufficient' and result['evidence'][0]['tags'] == ''
    assert any('/search/searchMain?' in url for url in calls) and result['failures']


def test_disabled_lookup_and_evidence_do_not_access_network(library):
    def fail(*_):
        raise AssertionError('unexpected network')
    lookup = ExternalLookup(library, Settings(domestic_enabled=False), domestic_fetch=fail)
    assert lookup.lookup(TRACK, 'domestic')['state'] == 'skipped'
    assert not lookup.evidence(TRACK)


def test_genie_uses_explicit_labels_and_country_genre_prefix():
    html = '<div class="info-zone"><h2 class="name">밤편지</h2><ul>'
    for file, value, label in [('txt_5', '아이유 (IU)', '아이유'), ('txt_6', '밤편지', '밤편지'), ('txt_7', '가요 / 발라드', '장르'), ('txt_8', '04:13', '재생시간')]:
        html += f'<li><span class="attr"><img src="/{file}.png" alt="{label}"></span><span class="value">{value}</span></li>'
    result = parse_page(GENIE, html + '</ul></div>')
    assert result['genres'] == ['발라드'] and result['raw_genre'] == '가요 / 발라드'
    assert matches(TRACK, result, 3)[0]


def test_only_approved_domestic_fields_reach_llm(library, root, song, fake_reader):
    from music_sorter.scanner import scan_library
    from music_sorter.llm import track_input
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    evidence = compare(TRACK, [source(), source('bugs')])['evidence']
    evidence[0].update(secret='not-allowed', raw_html='PRIVATE_HTML')
    inputs = track_input(track, evidence)
    assert inputs['external'][0]['verification'] == 'corroborated'
    assert inputs['external'][0]['tags'] == '발라드'
    assert 'PRIVATE_HTML' not in str(inputs) and 'not-allowed' not in str(inputs)


def test_domestic_review_ui_exposes_scope_sources_and_invalid_url_without_requests(qtbot, library, root, song, fake_reader):
    from music_sorter.scanner import scan_library
    from music_sorter.ui.domestic_dialog import DomesticReviewDialog
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    result = compare(TRACK, [source(), source('bugs', scope='single_album')])
    dialog = DomesticReviewDialog(library, Settings(), track, result)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table.rowCount() == 2 and '발라드' in dialog.summary.text()
    assert dialog.table.item(1, 4).text() == '한 곡짜리 앨범'
    assert MELON in dialog.source_label.text()
    dialog.url.setText('https://evil.test/private')
    dialog.start(True)
    assert dialog.worker is None and 'HTTPS' in dialog.status.text()


def test_domestic_review_refresh_worker_caches_conflict_without_changing_classification(qtbot, library, root, song, fake_reader, monkeypatch):
    from conftest import fake_snapshot
    from music_sorter.scanner import scan_library
    from music_sorter.ui.domestic_dialog import DomesticReviewDialog
    monkeypatch.setattr('music_sorter.scanner.read_snapshot', lambda path, cancel=None: {**fake_snapshot(path), **TRACK})
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    def fetch(url):
        if '/search/' in url:
            return search('melon' if 'melon.com' in url else 'bugs' if 'bugs.co.kr' in url else 'genie')
        if url == MELON:
            return melon('댄스')
        if url == BUGS:
            return bugs()
        if url == ALBUM:
            return album()
        raise ValueError('추가 출처 조회 실패')
    monkeypatch.setattr('music_sorter.domestic.fetch_page', fetch)
    dialog = DomesticReviewDialog(library, Settings(), track, {})
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start(False)
    assert not dialog.refresh_button.isEnabled() and dialog.stop.isEnabled()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert dialog.refresh_button.isEnabled() and not dialog.stop.isEnabled()
    assert dialog.result['state'] == 'conflict' and not dialog.result['agreed_genres']
    assert ExternalLookup(library, Settings()).cached(track, 'domestic')['state'] == 'conflict'
    assert library.track(track['id'])['review_state'] == 'unclassified'
    assert song.read_bytes() == b'original-audio'
