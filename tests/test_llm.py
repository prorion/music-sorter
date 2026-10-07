import copy
import json
from types import SimpleNamespace

import pytest

from music_sorter.classification import empty_classification
from music_sorter.llm import (ProviderClient, ProviderError, cache_key, cost_micro, input_bound,
                              parse_tracks, price, response_schema, track_input)


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_real_sdk_serializes_count_and_generation_without_network(provider):
    import httpx2
    import openai
    import anthropic
    requests = []
    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload['model'] == 'verified-test-model'
        if 'input_tokens' in request.url.path or 'count_tokens' in request.url.path:
            return httpx2.Response(200, json={'input_tokens': 50})
        if provider == 'anthropic':
            return httpx2.Response(200, json=dict(id='msg_test', type='message', role='assistant', model='verified-test-model',
                                                 content=[{'type': 'text', 'text': json.dumps({'tracks': [model_row()]})}],
                                                 stop_reason='end_turn', stop_sequence=None, usage={'input_tokens': 50, 'output_tokens': 250}))
        return httpx2.Response(200, json=dict(id='resp_test', object='response', created_at=1, status='completed', model='verified-test-model',
                                             output=[{'type': 'message', 'id': 'msg_test', 'role': 'assistant', 'status': 'completed',
                                                      'content': [{'type': 'output_text', 'text': json.dumps({'tracks': [model_row()]}), 'annotations': []}]}],
                                             usage={'input_tokens': 50, 'output_tokens': 250, 'total_tokens': 300}))
    transport = httpx2.Client(transport=httpx2.MockTransport(respond), follow_redirects=False, trust_env=False)
    sdk = (anthropic.Anthropic if provider == 'anthropic' else openai.OpenAI)(api_key='test-only', http_client=transport, max_retries=0)
    client = ProviderClient(provider, '', client=sdk)
    try:
        assert client.count_tokens('verified-test-model', [{'id': 'track1'}]) >= input_bound([{'id': 'track1'}])
        response = client.generate('verified-test-model', [{'id': 'track1'}], 1000)
        assert response['completed'] and response['usage']['output'] == 250
        assert len(requests) == 2
        body = json.loads(requests[1].content)
        if provider == 'openai':
            assert body['store'] is False and body['text']['format']['strict'] is True
        else:
            assert body['output_config']['format']['type'] == 'json_schema'
    finally:
        client.close()


def model_row(track_id='track1'):
    values = dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=[])
    return dict(id=track_id, classification={axis: dict(value=value, status='confirmed', confidence=.9, reason='모델의 사전 지식 · 외부 확인 없음')
                                          for axis, value in values.items()}, suggested_tags=[])


def test_parser_keeps_partial_success_and_rejects_invalid_genre():
    bad = model_row('track2')
    bad['classification']['subgenre']['value'] = ['찬송가 편곡']
    results, errors = parse_tracks(json.dumps({'tracks': [model_row(), bad]}), ['track1', 'track2', 'track3'])
    assert set(results) == {'track1'} and set(errors) == {'track2', 'track3'}
    assert results['track1']['classification']['concept']['status'] == 'confirmed'
    assert results['track1']['classification']['concept']['value'] == []


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -.1, 1.01, True, '0.9'])
def test_confidence_validation(bad):
    row = model_row()
    row['classification']['major']['confidence'] = bad
    results, errors = parse_tracks(json.dumps({'tracks': [row]}), ['track1'])
    assert not results and 'track1' in errors


def test_unresolved_and_concept_none_are_distinct():
    row = model_row()
    row['classification']['concept'].update(value=None, status='unresolved')
    result, _ = parse_tracks(json.dumps({'tracks': [row]}), ['track1'])
    assert result['track1']['classification']['concept']['value'] is None


@pytest.mark.parametrize('rows', [[model_row('wrong')], [model_row(), model_row()], [{'id': {'bad': True}}]])
def test_unknown_and_duplicate_ids_reject_whole_response(rows):
    with pytest.raises(ValueError):
        parse_tracks(json.dumps({'tracks': rows}), ['track1'])


def test_transmission_whitelist_and_cache_identity():
    track = dict(title='곡', artist='가수', album='앨범', version='', grade='B', duration=180,
                 metadata_json=dict(year='2020', genre='Pop', secret_key='private-key', has_priv=True),
                 classification=empty_classification(), path='C:/private/folder/music.mp3')
    payload = track_input(track, [{'service': 'musicbrainz', 'title': '곡', 'path': 'private-path', 'api_key': 'private-key'}])
    encoded = json.dumps(payload)
    assert 'private' not in encoded and 'path' not in encoded and 'has_priv' not in encoded
    first = cache_key('anthropic', 'claude-haiku-4-5', payload, 'first')
    other = copy.deepcopy(payload)
    other['version'] = 'live'
    assert first != cache_key('anthropic', 'claude-haiku-4-5', other, 'first')
    assert first != cache_key('anthropic', 'claude-sonnet-5-5', payload, 'first')
    assert first != cache_key('anthropic', 'claude-haiku-4-5', payload, 'escalate')
    assert input_bound([payload]) > len(encoded)


def test_provider_bodies_and_usage_accounting():
    for provider in ('anthropic', 'openai'):
        client = ProviderClient(provider, None, client=SimpleNamespace())
        body = client.body('model-id', [{'id': 'track1', 'title': '곡'}], 4096)
        assert body['model'] == 'model-id'
        if provider == 'openai':
            assert body['store'] is False and body['text']['format']['strict'] is True
            result = client.normalize(dict(id='resp', status='completed', output=[{'content': [{'type': 'output_text', 'text': '{}'}]}],
                                           usage={'input_tokens': 100, 'output_tokens': 50, 'input_tokens_details': {'cached_tokens': 30}}))
            assert result['usage'] == {'input': 70, 'output': 50, 'cached': 30, 'cache_write': 0}
        else:
            assert body['output_config']['format']['type'] == 'json_schema'
            result = client.normalize(dict(id='msg', stop_reason='end_turn', content=[{'type': 'text', 'text': '{}'}],
                                           usage={'input_tokens': 70, 'output_tokens': 50, 'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 0}))
            assert result['usage'] == {'input': 70, 'output': 50, 'cached': 30, 'cache_write': 0}


def test_pricing_ceil_unknown_model_and_batch_discount():
    pricing = price('anthropic', 'claude-haiku-4-5')
    assert cost_micro(pricing, 100, 20, 1) == 201
    assert price('anthropic', 'claude-haiku-4-5', 'batch')['input'] == '0.5'
    with pytest.raises(ValueError):
        price('anthropic', 'unknown-model')


def test_error_messages_never_expose_provider_body_or_key():
    def fail(**kwargs):
        error = Exception('test-key and private response body')
        error.status_code = 401
        raise error
    client = ProviderClient('anthropic', None, client=SimpleNamespace(messages=SimpleNamespace(create=fail)))
    with pytest.raises(ProviderError) as caught:
        client.generate('model', [], 100)
    assert caught.value.category == 'auth' and 'test-key' not in str(caught.value)


def test_missing_usage_retains_uncertain_state_and_missing_key_no_env_fallback(monkeypatch):
    client = ProviderClient('openai', None, client=SimpleNamespace())
    with pytest.raises(ProviderError):
        client.normalize({'status': 'completed', 'output': [], 'usage': None})
    monkeypatch.setenv('OPENAI_API_KEY', 'test-environment-key')
    with pytest.raises(ProviderError):
        ProviderClient('openai', '')


def test_prompt_cache_prefix_does_not_include_track_metadata():
    client = ProviderClient('anthropic', None, client=SimpleNamespace())
    client.contract = dict(system='fixed instruction', taxonomy={'major': {'가요': ['발라드']}}, schema=response_schema(), prompt_cache=True)
    first = client.body('model', [{'id': 'one', 'title': 'first song'}], 1000)
    second = client.body('model', [{'id': 'two', 'title': 'second song'}], 1000)
    assert first['system'] == second['system']
    assert first['system'][0]['cache_control'] == dict(type='ephemeral', ttl='5m')
    assert 'first song' not in first['system'][0]['text']
    assert first['messages'] != second['messages']


def test_source_sdk_selfcheck_remains_network_free():
    from music_sorter.selfcheck import verify_sdks
    result = verify_sdks()
    assert result['completed'] and result['network_requests'] == result['paid_requests'] == 0
    assert result['providers']['openai']['local_transport_requests'] == 7
    assert result['providers']['anthropic']['local_transport_requests'] == 7
    assert all(value['batch_completed'] for value in result['providers'].values())


@pytest.mark.parametrize('url', ['https://other.example/results', 'http://api.anthropic.com/results', 'https://api.anthropic.com:444/results', 'https://user:pass@api.anthropic.com/results'])
def test_result_urls_cannot_send_credentials_to_other_endpoint(url):
    import httpx2
    client = ProviderClient('anthropic', None, client=SimpleNamespace())
    with pytest.raises(ProviderError) as caught:
        client.check_endpoint(httpx2.Request('GET', url))
    assert caught.value.category == 'invalid'
    client.check_endpoint(httpx2.Request('GET', 'https://api.anthropic.com/v1/messages/batches/fixture/results'))
