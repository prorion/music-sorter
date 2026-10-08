"""Packaged SDK request/response check with transport fully replaced by local fixtures."""
import json


def verify_recycle(source, output):
    """Use two new media copies, never the caller's source, for native recycling QA."""
    import shutil
    from pathlib import Path
    from .database import Library
    from .scanner import scan_library
    from .recycle import DuplicateRemoval
    from .tag_io import digest

    output = Path(output)
    if output.exists():
        raise ValueError('휴지통 검증에는 새 검증 폴더가 필요합니다.')
    root = output / '한글 검증 사본'
    root.mkdir(parents=True)
    source = Path(source)
    source_hash = digest(source)
    for folder in ('유지', '삭제'):
        destination = root / folder / source.name
        destination.parent.mkdir()
        shutil.copy2(source, destination)
    library = Library(output / 'user-data' / 'library.sqlite3')
    scan_library(library, root)
    group = library.duplicate_groups()[0]
    target = next(item for item in group['tracks'] if Path(item['path']).parent.name == '삭제')
    survivor = next(item for item in group['tracks'] if item['id'] != target['id'])
    engine = DuplicateRemoval(library, root)
    result = engine.apply(engine.preview(group, [target['id']]))
    receipt = result['targets'][0]
    report = dict(completed=result['processed'] == 1 and result['failed'] == 0,
                  source_unchanged=digest(source) == source_hash,
                  survivor_unchanged=digest(Path(survivor['path'])) == source_hash,
                  deleted_copy_absent=not Path(target['path']).exists(),
                  recycle_copy_hash_matches=bool(receipt.get('recycle_path')) and digest(Path(receipt['recycle_path'])) == source_hash,
                  db_marked_missing=library.track(target['id'])['file_state'] == 'missing',
                  original_library_modified=False)
    (output / 'report.json').write_text(json.dumps(report, indent=2), 'utf-8')
    if not all(report[key] for key in ('completed', 'source_unchanged', 'survivor_unchanged',
                                      'deleted_copy_absent', 'recycle_copy_hash_matches', 'db_marked_missing')):
        raise ValueError('네이티브 휴지통 검증 실패. 검증 결과를 확인하세요.')
    return report


def verify_sdks():
    import anthropic
    import httpx2
    import openai
    from .llm import ProviderClient
    report = {}
    for provider in ('openai', 'anthropic'):
        calls = []
        if provider == 'anthropic':
            message = dict(id='msg_local', type='message', role='assistant', model='local-selfcheck-model',
                           content=[{'type': 'text', 'text': '{"tracks":[]}'}], stop_reason='end_turn', stop_sequence=None,
                           usage={'input_tokens': 50, 'output_tokens': 20})
        else:
            message = dict(id='resp_local', object='response', created_at=1, status='completed', model='local-selfcheck-model',
                           output=[{'type': 'message', 'id': 'msg_local', 'role': 'assistant', 'status': 'completed',
                                    'content': [{'type': 'output_text', 'text': '{"tracks":[]}', 'annotations': []}]}],
                           usage={'input_tokens': 50, 'output_tokens': 20, 'total_tokens': 70})

        def respond(request):
            calls.append(request.url.path)
            if request.url.path.endswith('/content'):
                row = dict(custom_id='fixture-request', response={'status_code': 200, 'body': message})
                return httpx2.Response(200, text=json.dumps(row) + '\n')
            if request.url.path.endswith('/results'):
                row = dict(custom_id='fixture-request', result={'type': 'succeeded', 'message': message})
                return httpx2.Response(200, text=json.dumps(row) + '\n')
            if '/files' in request.url.path:
                return httpx2.Response(200, json=dict(id='file_local', object='file', bytes=1, created_at=1, filename='music-sorter.jsonl', purpose='batch'))
            if '/batches' in request.url.path:
                return httpx2.Response(200, json=dict(id='batch_local', type='message_batch', object='batch', status='completed',
                                                     processing_status='ended', output_file_id='file_local', error_file_id=None,
                                                     results_url='https://api.anthropic.com/v1/messages/batches/batch_local/results'))
            payload = json.loads(request.content)
            if payload['model'] != 'local-selfcheck-model':
                raise ValueError('검증 모델 값이 변경되었습니다.')
            if 'input_tokens' in request.url.path or 'count_tokens' in request.url.path:
                return httpx2.Response(200, json={'input_tokens': 50})
            return httpx2.Response(200, json=message)

        transport = httpx2.Client(transport=httpx2.MockTransport(respond), follow_redirects=False, trust_env=False)
        sdk = (anthropic.Anthropic if provider == 'anthropic' else openai.OpenAI)(api_key='local-fixture-only', http_client=transport, max_retries=0)
        client = ProviderClient(provider, '', client=sdk)
        try:
            stage = 'count_tokens'
            bound = client.count_tokens('local-selfcheck-model', [{'id': 'fixture'}])
            stage = 'generate'
            response = client.generate('local-selfcheck-model', [{'id': 'fixture'}], 1024)
            if not response['completed'] or response['usage']['output'] != 20 or len(calls) != 2 or bound < 50:
                raise ValueError('SDK 검증 응답을 확인할 수 없습니다.')
            stage = 'batch_upload'
            batch = [dict(id='fixture-request', body=client.body('local-selfcheck-model', [{'id': 'fixture'}], 1024))]
            upload = client.upload_batch(batch)
            stage = 'batch_submit'
            remote = client.submit_batch(batch, upload, 'fixture-job')
            stage = 'batch_status'
            status, files = client.batch_status(remote)
            stage = 'batch_results'
            results = list(client.batch_results(remote, files))
            if remote != 'batch_local' or status not in {'ended', 'completed'} or results[0][0] != 'fixture-request' or not results[0][1]['completed']:
                raise ValueError('Batch 검증 응답을 확인할 수 없습니다.')
            stage = 'batch_cancel'
            client.cancel_batch(remote)
            report[provider] = dict(local_transport_requests=len(calls), completed=True, output_tokens=20, batch_completed=True)
        except Exception as error:
            # These clients have synthetic keys and a replaced transport. Never record messages.
            causes = []
            frames = []
            cause = error
            while cause is not None and len(causes) < 5:
                causes.append(type(cause).__module__ + '.' + type(cause).__name__)
                tb = cause.__traceback__
                while tb is not None:
                    frames.append(dict(function=tb.tb_frame.f_code.co_name, line=tb.tb_lineno))
                    tb = tb.tb_next
                cause = cause.__context__
            report[provider] = dict(completed=False, stage=stage, error_types=causes,
                                    frames=frames[-12:], local_transport_requests=len(calls))
        finally:
            client.close()
    return dict(providers=report, completed=all(item['completed'] for item in report.values()),
                network_requests=0, paid_requests=0)
