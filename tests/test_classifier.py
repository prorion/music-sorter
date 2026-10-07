import json

import pytest

from music_sorter.classifier import Classifier, budget_micro, merge_result
from music_sorter.llm import ProviderError, input_bound, parse_tracks
from music_sorter.scanner import ScanControl, scan_library
from test_llm import model_row


class Client:
    provider = 'anthropic'
    def __init__(self, action=None):
        self.calls = 0
        self.action = action
    def count_tokens(self, model, inputs):
        return input_bound(inputs)
    def generate(self, model, inputs, max_tokens):
        self.calls += 1
        if self.action:
            result = self.action(self.calls, inputs)
            if result:
                raise ProviderError(result)
        return dict(id='remote1', completed=True, text=json.dumps({'tracks': [model_row(row['id']) for row in inputs]}),
                    usage=dict(input=100, output=500, cached=0, cache_write=0))


def prepared(library, root, song, fake_reader, budget='1', purpose='classify'):
    scan_library(library, root)
    engine = Classifier(library)
    ids = [row['id'] for row in library.list_tracks()[0]]
    job = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget=budget, purpose=purpose, control=ScanControl())
    return engine, job, ids


def test_budget_reservation_completion_and_no_paid_prepare(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    client = Client()
    assert client.calls == 0 and engine.summary(job)['counts'] == {'prepared': 1}
    result = engine.run(job, client, ScanControl())
    assert result['actual'] == 2600 and result['reserved'] == 0
    assert result['counts'] == {'completed': 1} and client.calls == 1
    assert library.track(ids[0])['review_state'] == 'confirmed'
    engine.run(job, client)
    assert client.calls == 1


def test_budget_blocks_before_paid_request(library, root, song, fake_reader):
    engine, job, _ = prepared(library, root, song, fake_reader, budget='.001')
    client = Client()
    result = engine.run(job, client)
    assert result['counts'] == {'prepared': 1} and result['actual'] == 0 and client.calls == 0
    engine.increase_budget(job, '1')
    engine.run(job, client)
    assert client.calls == 1


def test_file_change_before_submission_sends_nothing(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    song.write_bytes(b'changed-after-preparation')
    client = Client()
    result = engine.run(job, client)
    assert client.calls == 0 and result['counts'] == {'blocked': 1}
    assert result['actual'] == result['reserved'] == 0


def test_abandoned_plan_releases_track_for_new_model_plan(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    assert engine.cancel_prepared(job) == 1
    new = engine.prepare(ids, provider='anthropic', model='claude-sonnet-5-5', budget=1)
    assert engine.summary(new)['counts'] == {'prepared': 1}
    assert engine.summary(job)['actual'] == engine.summary(job)['reserved'] == 0


def test_custom_output_limit_is_reserved_and_retry_limit_is_frozen(library, root, song, fake_reader):
    scan_library(library, root)
    engine = Classifier(library)
    ids = [row['id'] for row in library.list_tracks()[0]]
    job = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget='1',
                         tracks_per_request=1, max_output_tokens_per_track=2000, max_retries=0, timeout_seconds=30)
    reservations = []
    def limited(*_):
        reservations.append(engine.summary(job)['reserved'])
        return 'retryable'
    client = Client(limited)
    engine.run(job, client)
    assert client.calls == 1 and engine.retry_failed(job) == 0
    assert engine.job(job)['options']['max_tokens_per_track'] == 2000
    assert engine.job(job)['options']['timeout_seconds'] == 30
    assert reservations[0] >= 10000


def test_unknown_holds_reservation_and_prevents_second_job(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    client = Client(lambda *_: 'unknown')
    result = engine.run(job, client)
    assert result['reserved'] > 0 and result['counts'] == {'unknown': 1} and result['actual'] == 0
    newer = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget=1, purpose='reclassify')
    assert engine.summary(newer)['counts'] == {}
    engine.run(job, client)
    assert client.calls == 1


def test_unknown_requires_confirmation_and_preserves_audit(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    client = Client(lambda *_: 'unknown')
    engine.run(job, client)
    request = engine.uncertain_requests(job)[0]
    for confirmed, reason in [(False, 'fixture provider check'), (True, '')]:
        with pytest.raises(ValueError):
            engine.confirm_unprocessed(job, [request['id']], reason, confirmed=confirmed)
        assert engine.summary(job)['reserved'] == request['reserved']
    result = engine.confirm_unprocessed(job, [request['id']], 'fixture provider confirmed no processing or charge', confirmed=True)
    assert result['released'] == request['reserved'] and engine.summary(job)['reserved'] == 0
    assert engine.summary(job)['counts'] == {'cancelled': 1} and client.calls == 1
    with pytest.raises(ValueError):
        engine.confirm_unprocessed(job, [request['id']], 'again', confirmed=True)
    fresh = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget='1')
    assert engine.summary(fresh)['counts'] == {'prepared': 1}
    with library.connection() as db:
        audit = db.execute('SELECT reason FROM llm_requests WHERE id=?', (request['id'],)).fetchone()[0]
    assert 'fixture provider' in audit


def test_unknown_resolution_rolls_back_on_stale_request(library, root, song, fake_reader):
    engine, job, _ = prepared(library, root, song, fake_reader)
    engine.run(job, Client(lambda *_: 'unknown'))
    request = engine.uncertain_requests(job)[0]
    with pytest.raises(ValueError):
        engine.confirm_unprocessed(job, [request['id'], 'not-in-this-job'], 'provider check', confirmed=True)
    assert engine.summary(job)['reserved'] == request['reserved']
    assert engine.uncertain_requests(job)[0]['id'] == request['id']


def test_cache_write_premium_reserved_before_generation(library, root, song, fake_reader):
    from music_sorter.llm import reservation_cost, usage_cost
    engine, job, _ = prepared(library, root, song, fake_reader)
    options = engine.job(job)['options']
    assert options['contract']['prompt_cache'] is True
    assert reservation_cost(options, 1000, 100) == 1750
    assert usage_cost(options, dict(input=100, output=100, cached=500, cache_write=400)) == 1150
    old = dict(options, contract={})
    assert reservation_cost(old, 1000, 100) == 1500


def test_429_max_four_auth_one_and_preserves_classification(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    client = Client(lambda *_: 'retryable')
    result = engine.run(job, client)
    assert client.calls == 4 and result['reserved'] == 0 and result['counts'] == {'failed': 1}
    assert library.track(ids[0])['review_state'] == 'unclassified'
    newjob = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget=1, purpose='reclassify')
    auth = Client(lambda *_: 'auth')
    engine.run(newjob, auth)
    assert auth.calls == 1 and engine.summary(newjob)['reserved'] == 0


def test_stale_manual_edit_becomes_proposal(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    def edit(*_):
        track = library.track(ids[0])
        library.save_manual(ids[0], {'major': '재즈'}, track['revision'])
    engine.run(job, Client(edit))
    assert engine.summary(job)['counts'] == {'proposal': 1}
    assert library.track(ids[0])['classification']['major']['value'] == '재즈'


def test_file_changed_after_submission_becomes_proposal(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    engine.run(job, Client(lambda *_: song.write_bytes(b'externally-changed') and None))
    assert engine.summary(job)['counts'] == {'proposal': 1}
    assert library.track(ids[0])['review_state'] == 'unclassified'


def test_received_crash_recovery_no_duplicate_charge(library, root, song, fake_reader, monkeypatch):
    engine, job, ids = prepared(library, root, song, fake_reader)
    original = engine.apply_received
    calls = []
    def crash(job_id):
        if calls:
            raise RuntimeError('crash after receiving')
        calls.append(True)
        original(job_id)
    monkeypatch.setattr(engine, 'apply_received', crash)
    client = Client()
    with pytest.raises(RuntimeError):
        engine.run(job, client)
    assert engine.summary(job)['actual'] == 2600
    fresh = Classifier(library)
    fresh.recover()
    assert fresh.summary(job)['counts'] == {'completed': 1}
    fresh.run(job, client)
    assert client.calls == 1 and fresh.summary(job)['actual'] == 2600


def test_cancelled_prepare_and_no_invalid_budget_job(library, root, song, fake_reader):
    scan_library(library, root)
    engine = Classifier(library)
    before = len(library.jobs())
    with pytest.raises(ValueError):
        engine.prepare([], provider='anthropic', model='claude-haiku-4-5', budget='NaN')
    assert len(library.jobs()) == before
    control = ScanControl()
    control.cancelled.set()
    job = engine.prepare([library.list_tracks()[0][0]['id']], provider='anthropic', model='claude-haiku-4-5', budget=1, control=control)
    assert engine.summary(job)['counts'] == {}


def test_protected_major_keeps_compatible_pair_and_other_axes():
    proposal = parse_tracks(json.dumps({'tracks': [model_row()]}), ['track1'])[0]['track1']['classification']
    original = json.loads(json.dumps(proposal))
    original['major'].update(value='CCM', protected=True)
    original['subgenre']['value'] = ['워십/찬양']
    proposal['subgenre']['value'] = ['댄스']
    updated, conflicts = merge_result(original, proposal, 'reclassify')
    assert updated['major']['value'] == 'CCM' and updated['subgenre']['value'] == ['워십/찬양']
    assert {'major', 'subgenre'} <= set(conflicts)


class BatchClient(Client):
    def body(self, model, inputs, max_tokens):
        return {'inputs': inputs}
    def upload_batch(self, requests):
        self.requests = requests
        return 'upload1'
    def submit_batch(self, requests, upload_id, job_id):
        self.calls += 1
        return 'batch1'
    def batch_status(self, remote_id):
        return 'ended', []
    def batch_results(self, remote_id, files):
        for request in reversed(self.requests):
            yield request['id'], dict(id='message1', completed=True,
                                      text=json.dumps({'tracks': [model_row(r['id']) for r in request['body']['inputs']]}),
                                      usage=dict(input=100, output=500, cached=0, cache_write=0))
    def cancel_batch(self, remote_id):
        self.cancelled = remote_id


def test_batch_persist_collect_repeated_without_double_cost(library, root, song, fake_reader):
    scan_library(library, root)
    engine = Classifier(library)
    job = engine.prepare([r['id'] for r in library.list_tracks()[0]], provider='anthropic', model='claude-haiku-4-5', budget=1, execution='batch')
    client = BatchClient()
    result = engine.submit_batch(job, client)
    assert result['counts'] == {'remote': 1} and result['reserved'] > 0 and result['actual'] == 0
    fresh = Classifier(library)
    fresh.recover()
    fresh.cancel_remote(job, client)
    assert fresh.summary(job)['reserved'] > 0 and client.cancelled == 'batch1'
    result = fresh.collect_batch(job, client)
    assert result['counts'] == {'completed': 1} and result['reserved'] == 0 and result['actual'] == 1300
    again = fresh.collect_batch(job, client)
    assert again['actual'] == 1300 and client.calls == 1


def test_batch_creation_unknown_never_recreates(library, root, song, fake_reader):
    scan_library(library, root)
    engine = Classifier(library)
    job = engine.prepare([r['id'] for r in library.list_tracks()[0]], provider='anthropic', model='claude-haiku-4-5', budget=1, execution='batch')
    class Unknown(BatchClient):
        def submit_batch(self, *args):
            self.calls += 1
            raise ProviderError('unknown')
    client = Unknown()
    result = engine.submit_batch(job, client)
    assert result['counts'] == {'unknown': 1} and result['reserved'] > 0
    engine.submit_batch(job, client)
    assert client.calls == 1


def test_exhausted_retry_cannot_reset_limit(library, root, song, fake_reader):
    engine, job, ids = prepared(library, root, song, fake_reader)
    client = Client(lambda *_: 'retryable')
    engine.run(job, client)
    assert engine.retry_failed(job) == 0
    newer = engine.prepare(ids, provider='anthropic', model='claude-haiku-4-5', budget=1)
    assert engine.summary(newer)['counts'] == {}


def test_partial_response_keeps_valid_track(library, root, song, fake_reader):
    (root / '다른가수 - 다른곡.mp3').write_bytes(b'different-audio')
    engine, job, ids = prepared(library, root, song, fake_reader)
    class Partial(Client):
        def generate(self, model, inputs, max_tokens):
            response = super().generate(model, inputs, max_tokens)
            payload = json.loads(response['text'])
            payload['tracks'][1]['classification']['major']['value'] = 'invented'
            response['text'] = json.dumps(payload)
            return response
    engine.run(job, Partial())
    assert engine.summary(job)['counts'] == {'completed': 1, 'failed': 1}


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'Infinity', '.0000001', 'invalid'])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        budget_micro(value)
