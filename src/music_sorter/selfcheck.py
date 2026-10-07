"""Packaged SDK request/response check with transport fully replaced by local fixtures."""
import json


def verify_sdks():
    import anthropic
    import httpx2
    import openai
    from .llm import ProviderClient
    report = {}
    for provider in ('openai', 'anthropic'):
        calls = []

        def respond(request):
            payload = json.loads(request.content)
            if payload['model'] != 'local-selfcheck-model':
                raise ValueError('검증 모델 값이 변경되었습니다.')
            calls.append(request.url.path)
            if 'input_tokens' in request.url.path or 'count_tokens' in request.url.path:
                return httpx2.Response(200, json={'input_tokens': 50})
            if provider == 'anthropic':
                return httpx2.Response(200, json=dict(id='msg_local', type='message', role='assistant', model='local-selfcheck-model',
                    content=[{'type': 'text', 'text': '{"tracks":[]}'}], stop_reason='end_turn', stop_sequence=None,
                    usage={'input_tokens': 50, 'output_tokens': 20}))
            return httpx2.Response(200, json=dict(id='resp_local', object='response', created_at=1, status='completed', model='local-selfcheck-model',
                output=[{'type': 'message', 'id': 'msg_local', 'role': 'assistant', 'status': 'completed',
                         'content': [{'type': 'output_text', 'text': '{"tracks":[]}', 'annotations': []}]}],
                usage={'input_tokens': 50, 'output_tokens': 20, 'total_tokens': 70}))

        transport = httpx2.Client(transport=httpx2.MockTransport(respond), follow_redirects=False, trust_env=False)
        sdk = (anthropic.Anthropic if provider == 'anthropic' else openai.OpenAI)(api_key='local-fixture-only', http_client=transport, max_retries=0)
        client = ProviderClient(provider, '', client=sdk)
        try:
            bound = client.count_tokens('local-selfcheck-model', [{'id': 'fixture'}])
            response = client.generate('local-selfcheck-model', [{'id': 'fixture'}], 1024)
            if not response['completed'] or response['usage']['output'] != 20 or len(calls) != 2 or bound < 50:
                raise ValueError('SDK 검증 응답을 확인할 수 없습니다.')
            report[provider] = dict(local_transport_requests=len(calls), completed=True, output_tokens=20)
        finally:
            client.close()
    return dict(providers=report, network_requests=0, paid_requests=0)
