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
    assert len(calls) == 2 and len(lookup.evidence(item)) == 2
    with library.connection() as db:
        assert 'secret-key' not in str([tuple(row) for row in db.execute('SELECT * FROM external_cache')])
    lookup.lookup(item, 'musicbrainz', refresh=True)
    assert len(calls) == 3


def test_missing_contact_key_no_network(library):
    def fail(*_):
        raise AssertionError('must not call')
    lookup = ExternalLookup(library, Settings(), fetch=fail)
    assert lookup.lookup(track(), 'musicbrainz')['state'] == 'skipped'
    assert lookup.lookup(track(), 'lastfm')['state'] == 'skipped'
