from music_sorter.external import ExternalLookup, match_musicbrainz, match_lastfm
from music_sorter.scanner import scan_library
from music_sorter.settings import Settings


def candidate(identity='11111111-1111-1111-1111-111111111111', title='제목', artist='가수', length=180000):
    return dict(id=identity, title=title, length=length, **{'artist-credit': [{'name': artist}], 'tags': [{'name': 'ballad'}]})


def track():
    return dict(title='제목', artist='가수', album='', version='', duration=180, hash='hash')


def test_matching_requires_unique_exact_recording():
    result = match_musicbrainz(track(), {'recordings': [candidate()], 'count': 1})
    assert result['state'] == 'matched' and result['evidence'][0]['recording_id']
    assert match_musicbrainz(track(), {'recordings': [candidate(), candidate('22222222-2222-2222-2222-222222222222')], 'count': 2})['state'] == 'ambiguous'
    assert match_musicbrainz(track(), {'recordings': [candidate()], 'count': 30})['state'] == 'ambiguous'
    for item in (candidate(title='제목 (Live)'), candidate(artist='다른 가수'), candidate(length=240000)):
        assert not match_musicbrainz(track(), {'recordings': [item], 'count': 1})['evidence']


def test_album_conflict_and_lastfm_reference_only():
    song = track()
    song['album'] = '내 앨범'
    row = candidate()
    row['releases'] = [{'title': '다른 앨범'}]
    assert not match_musicbrainz(song, {'recordings': [row], 'count': 1})['evidence']
    result = match_lastfm(track(), {'track': {'name': '제목', 'artist': {'name': '가수'}, 'toptags': {'tag': [{'name': 'ballad'}]}}})
    assert result['state'] == 'reference' and 'recording_id' not in result['evidence'][0]


def test_cache_has_no_key_and_reuses_complete_response(library, root, song, fake_reader):
    scan_library(library, root)
    item = library.list_tracks()[0][0]
    calls = []
    def fetch(service, params):
        calls.append((service, params))
        return {'recordings': [candidate()], 'count': 1} if service == 'musicbrainz' else {'track': {'name': '제목', 'artist': {'name': '가수'}}}
    lookup = ExternalLookup(library, Settings(musicbrainz_contact='user@example.test'), lastfm_key='secret-key', fetch=fetch)
    assert lookup.lookup(item, 'musicbrainz')['state'] == 'matched'
    assert lookup.lookup(item, 'musicbrainz')['cached']
    lookup.lookup(item, 'lastfm')
    assert len(calls) == 3 and len(lookup.evidence(item)) == 2
    with library.connection() as db:
        assert 'secret-key' not in str([tuple(row) for row in db.execute('SELECT * FROM external_cache')])
    lookup.lookup(item, 'musicbrainz', refresh=True)
    assert len(calls) == 4


def test_missing_contact_key_no_network(library):
    def fail(*_):
        raise AssertionError('must not call')
    lookup = ExternalLookup(library, Settings(), fetch=fail)
    assert lookup.lookup(track(), 'musicbrainz')['state'] == 'skipped'
    assert lookup.lookup(track(), 'lastfm')['state'] == 'skipped'


def test_duration_setting_invalidates_cache(library):
    settings = Settings(musicbrainz_contact='user@example.test', duplicate_tolerance_seconds=5)
    calls = []
    def fetch(service, params):
        calls.append(params)
        return dict(recordings=[candidate(length=184000)], count=1)
    lookup = ExternalLookup(library, settings, fetch=fetch)
    assert lookup.lookup(track(), 'musicbrainz')['state'] == 'matched'
    settings.duplicate_tolerance_seconds = 2
    assert lookup.cached(track(), 'musicbrainz') is None
    assert lookup.lookup(track(), 'musicbrainz')['state'] == 'ambiguous'
    assert len(calls) == 2


def test_valid_existing_id_has_priority_and_conflict_never_falls_back(library):
    item = track()
    identity = candidate()['id']
    item['metadata_json'] = dict(recording_ids=[identity], isrcs=['USAAA2600001'])
    calls = []
    def fetch(service, params):
        calls.append(params)
        return candidate(artist='다른 가수')
    result = ExternalLookup(library, Settings(musicbrainz_contact='user@example.test'), fetch=fetch).lookup(item, 'musicbrainz')
    assert len(calls) == 1 and calls[0]['_id'] == identity and calls[0]['_entity'] == 'recording'
    assert result['state'] == 'ambiguous' and result['evidence'] == []


def test_isrc_lookup_checks_all_candidates_and_metadata_ids(library):
    from music_sorter.scanner import recording_identifiers
    from mutagen.id3 import ID3, TSRC, UFID, TXXX
    tags = ID3()
    identity = candidate()['id']
    tags.add(UFID(owner='http://musicbrainz.org', data=identity.encode()))
    tags.add(TXXX(desc='MusicBrainz Recording Id', text=['invalid']))
    tags.add(TSRC(text=['US-AAA-26-00001']))
    assert recording_identifiers(tags) == dict(recording_ids=[identity], isrcs=['USAAA2600001'])
    item = track()
    item['metadata_json'] = dict(isrcs=['USAAA2600001'])
    calls = []
    def fetch(service, params):
        calls.append(params)
        return dict(recordings=[candidate(), candidate('22222222-2222-2222-2222-222222222222')])
    result = ExternalLookup(library, Settings(musicbrainz_contact='user@example.test'), fetch=fetch).lookup(item, 'musicbrainz')
    assert result['state'] == 'ambiguous' and calls[0]['_entity'] == 'isrc'


def test_manual_candidate_selection_is_snapshot_guarded(library, root, song, fake_reader):
    import pytest
    scan_library(library, root)
    item = library.list_tracks()[0][0]
    lookup = ExternalLookup(library, Settings(musicbrainz_contact='user@example.test'), fetch=lambda *_: dict(recordings=[candidate(), candidate('22222222-2222-2222-2222-222222222222')], count=2))
    lookup.lookup(item, 'musicbrainz')
    expected = lookup.cached(item, 'musicbrainz')
    selected = lookup.choose_candidate(item['id'], candidate()['id'], expected, item['revision'], '앨범 크레딧의 연주자와 녹음을 확인')
    assert selected['manual_selection']['recording_id'] == candidate()['id']
    assert library.track(item['id'])['classification'] == item['classification']
    assert song.read_bytes() == b'original-audio'
    with pytest.raises(ValueError):
        lookup.choose_candidate(item['id'], candidate()['id'], expected, item['revision'], '이전 캐시')
    fresh = lookup.cached(item, 'musicbrainz')
    song.write_bytes(b'external-change')
    with pytest.raises(ValueError):
        lookup.choose_candidate(item['id'], candidate()['id'], fresh, item['revision'], '변경된 파일')


def test_artist_tags_cache_shared_across_songs_and_reference_only(library):
    calls = []
    def fetch(service, params):
        calls.append(params['method'])
        if params['method'] == 'artist.getTopTags':
            return {'toptags': {'@attr': {'artist': '가수'}, 'tag': [{'name': 'jazz'}]}}
        return {'track': {'name': params['track'], 'artist': {'name': '가수'}}}
    lookup = ExternalLookup(library, Settings(), lastfm_key='fixture', fetch=fetch)
    first = lookup.lookup(track(), 'lastfm')
    second = track()
    second.update(title='두 번째', hash='another')
    lookup.lookup(second, 'lastfm')
    assert calls.count('artist.getTopTags') == 1
    assert first['artist_reference']['state'] == 'reference'
    assert first['evidence'][-1]['service'] == 'lastfm_artist' and 'recording_id' not in first['evidence'][-1]
