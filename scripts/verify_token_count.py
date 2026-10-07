"""Explicit Claude token-count probe. Synthetic input, no generation endpoint."""
import argparse
import json
import re
from pathlib import Path

from music_sorter.catalog import active_catalog
from music_sorter.llm import ProviderClient, ProviderError, SYSTEM, response_schema
from music_sorter.settings import CredentialStore, Settings, data_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live or args.output.exists():
        parser.error('--live와 새 출력 파일이 필요합니다.')
    settings = Settings.load(data_directory() / 'settings.json')
    client = None
    api_key = CredentialStore().get('anthropic')
    report = dict(provider='anthropic', model=settings.classify_model, generation_requests=0, batch_submissions=0,
                  input_source='synthetic_fixture', pricing_reference='https://platform.claude.com/docs/en/build-with-claude/token-counting')
    if settings.classify_provider != 'anthropic':
        raise ValueError('Claude 기본 분류 모델의 무료 입력 계량만 검증합니다.')
    try:
        client = ProviderClient('anthropic', api_key, settings.anthropic_workspace_id)
        client.contract = dict(system=SYSTEM, taxonomy=active_catalog(), schema=response_schema(), prompt_cache=True)
        bound = client.count_tokens(settings.classify_model, [dict(id='local-fixture', title='local verification', artist='local fixture', album='', version='', grade='D', duration=180, protected={}, external=[])])
        report.update(status='verified', conservative_input_bound=bound, token_count_requests=1)
    except ProviderError as error:
        report.update(status='error', category=error.category)
        cause = error.__context__
        if cause is not None:
            report['status_code'] = getattr(cause, 'status_code', None)
            body = getattr(cause, 'body', {})
            details = body.get('error', {}) if isinstance(body, dict) else {}
            message = details.get('message', '') if isinstance(details, dict) else ''
            report['field_hints'] = [field for field in ('output_config', 'format', 'schema', 'cache_control', 'model', 'workspace') if field in str(message)]
            # Only synthetic inputs were sent; redact credentials before retaining diagnostics.
            safe = str(message).replace(api_key or '[no-key]', '[redacted]')
            if settings.anthropic_workspace_id:
                safe = safe.replace(settings.anthropic_workspace_id, '[workspace]')
            report['safe_diagnostic'] = re.sub(r'sk-[A-Za-z0-9_-]+', '[redacted]', safe)[:500]
    except Exception as error:
        report.update(status='error', error_type=type(error).__name__)
    finally:
        if client:
            client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
