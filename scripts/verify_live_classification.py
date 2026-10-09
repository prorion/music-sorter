"""Explicit paid Claude probe on six copies; durable phases and aggregate budget."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

from music_sorter.classifier import Classifier, budget_micro
from music_sorter.database import Library
from music_sorter.llm import ProviderClient, usage_cost
from music_sorter.scanner import scan_library
from music_sorter.settings import CredentialStore, Settings, data_directory


TITLES = ['A Cup Of Coffee', 'Beautiful', 'BAE BAE', 'Autumn Longing', 'Blue Moon', 'A Song']
BUDGETS = {'sync': '1.5', 'cache': '.1', 'batch': '2', 'cancel': '.4', 'cancel_fixed': '.5'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')


def setup(output, source, cap):
    if output.exists() or cap != Decimal('5'):
        raise ValueError('새 검증 폴더와 승인된 US$5 상한을 사용하세요.')
    with sqlite3.connect('file:' + source.as_posix() + '?mode=ro', uri=True) as db:
        rows = [db.execute("SELECT path,title,artist FROM tracks WHERE title=? AND file_state='ready' ORDER BY path LIMIT 1", (title,)).fetchone() for title in TITLES]
    if any(row is None for row in rows):
        raise ValueError('여섯 곡의 사본을 찾을 수 없습니다.')
    root = output / 'sample-library'
    root.mkdir(parents=True)
    sources = []
    for index, (name, title, artist) in enumerate(rows):
        original = Path(name)
        target = root / f'{index:02d}' / original.name
        target.parent.mkdir()
        expected = digest(original)
        shutil.copy2(original, target)
        assert digest(target) == expected
        sources.append(dict(source=str(original), copy=str(target), hash=expected, title=title, artist=artist))
    data = output / 'user-data'
    library = Library(data / 'music-sorter.sqlite3')
    result = scan_library(library, root)
    assert result['new'] == 6 and result['failed'] == 0
    with library.connection() as db:
        ids = [db.execute('SELECT id FROM tracks WHERE path=?', (item['copy'],)).fetchone()[0] for item in sources]
    protected = library.track(ids[1])
    library.save_manual(ids[1], {'major': '가요'}, protected['revision'])
    Settings(music_root=str(root), classify_provider='anthropic', classify_model='claude-haiku-4-5').save(data / 'settings.json')
    manifest = dict(cap_micro=budget_micro(cap), model='claude-haiku-4-5', jobs={}, sources=sources, ids=ids,
                    budgets=BUDGETS, accuracy_evaluated=False, source_db_sha256=digest(source), source_db=str(source))
    write(output / 'manifest.json', manifest)
    return manifest


def prepare(engine, manifest, name, ids, purpose='classify', execution='sync'):
    if name in manifest['jobs']:
        return manifest['jobs'][name]
    assert sum(budget_micro(value) for value in BUDGETS.values()) <= manifest['cap_micro']
    job = engine.prepare(ids, provider='anthropic', model=manifest['model'], budget=BUDGETS[name], purpose=purpose,
                         execution=execution, tracks_per_request=1, max_output_tokens_per_track=2000,
                         timeout_seconds=120, max_retries=0)
    assert engine.summary(job)['counts'] == {'prepared': len(ids)}
    manifest['jobs'][name] = job
    return job


def report(output, engine, manifest):
    jobs = {name: engine.summary(job) for name, job in manifest['jobs'].items()}
    usage = []
    with engine.library.connection() as db:
        for row in db.execute('SELECT * FROM llm_requests ORDER BY created_at'):
            item = dict(row)
            response = json.loads(item['response']) if item['response'] else None
            if response:
                options = engine.job(item['job_id'])['options']
                assert usage_cost(options, response['usage']) == item['actual']
                assert 0 <= item['actual'] <= item['reserved']
            usage.append(dict(id=item['id'], state=item['state'], attempts=item['attempts'], remote_id=item['remote_id'],
                              actual_micro=item['actual'], reservation_micro=item['reserved'], usage=response['usage'] if response else None))
    assert sum(job['budget'] for job in jobs.values()) <= manifest['cap_micro']
    assert sum(job['actual'] + job['reserved'] for job in jobs.values()) <= manifest['cap_micro']
    for item in manifest['sources']:
        assert digest(item['source']) == digest(item['copy']) == item['hash']
    assert digest(manifest['source_db']) == manifest['source_db_sha256']
    tracks = [engine.library.track(identifier) for identifier in manifest['ids']]
    fields = ['id', 'title', 'artist', 'review_state', 'classification']
    value = dict(model=manifest['model'], cap_micro=manifest['cap_micro'], actual_micro=sum(j['actual'] for j in jobs.values()),
                 reserved_micro=sum(j['reserved'] for j in jobs.values()), originals_and_copies_unchanged=True,
                 existing_db_unchanged=True, accuracy_evaluated=False, jobs=jobs, requests=usage,
                 classifications=[{key: track[key] for key in fields} for track in tracks],
                 checks=manifest.get('checks', {}), error=manifest.get('error'))
    write(output / 'report.json', value)
    print(json.dumps({key: value[key] for key in ('model', 'cap_micro', 'actual_micro', 'reserved_micro', 'checks', 'error')}, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--budget', type=Decimal, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-db', type=Path)
    parser.add_argument('--phase', choices=['start', 'submit', 'collect', 'cancel', 'cancel-recheck', 'recheck-local'], required=True)
    args = parser.parse_args()
    if args.phase in {'start', 'submit'}:
        raise ValueError('이 스크립트는 과거 예산 제한 검증용입니다. 새 유료 제출은 지원하지 않습니다. 기존 결과 수집·확인만 가능합니다.')
    output = args.output.resolve()
    if args.budget != Decimal('5'):
        raise ValueError('이 검증의 승인 상한은 US$5입니다.')
    manifest = setup(output, args.source_db.resolve(), args.budget) if args.phase == 'start' else json.loads((output / 'manifest.json').read_text('utf-8'))
    library = Library(output / 'user-data' / 'music-sorter.sqlite3')
    engine = Classifier(library)
    engine.recover()
    settings = Settings.load(data_directory() / 'settings.json')
    client = ProviderClient('anthropic', CredentialStore().get('anthropic'), settings.anthropic_workspace_id, timeout=120)
    manifest.setdefault('checks', {})
    try:
        if args.phase == 'start':
            job = prepare(engine, manifest, 'sync', manifest['ids'][:3], purpose='reclassify')
            write(output / 'manifest.json', manifest)
            engine.run(job, client)
            rows = engine.rows(job)
            assert all(row['state'] in {'completed', 'proposal'} for row in rows), '실제 동기 응답 검증 실패'
            protected = library.track(manifest['ids'][1])['classification']['major']
            assert protected['protected'] and protected['value'] == '가요' and protected['source'] == 'manual'
            with library.connection() as db:
                before = db.execute('SELECT count(*) FROM llm_requests').fetchone()[0]
            engine.run(job, client)
            cached = prepare(engine, manifest, 'cache', manifest['ids'][:1], purpose='reclassify')
            write(output / 'manifest.json', manifest)
            engine.run(cached, client)
            with library.connection() as db:
                assert db.execute('SELECT count(*) FROM llm_requests').fetchone()[0] == before
            assert engine.summary(cached)['actual'] == 0
            manifest['checks'].update(sync_valid=True, manual_protected=True, idempotent_reexecution=True, cache_no_generation=True)
        elif args.phase == 'submit':
            if not manifest['checks'].get('sync_valid'):
                raise ValueError('실제 동기 응답 확인 후 Batch를 제출하세요.')
            job = prepare(engine, manifest, 'batch', manifest['ids'][3:5], execution='batch')
            write(output / 'manifest.json', manifest)
            # Restarting this phase never re-submits an accepted remote request.
            if engine.summary(job)['counts'].get('prepared'):
                engine.submit_batch(job, client)
            assert engine.summary(job)['counts'] == {'remote': 2}, 'Batch 제출 상태 확인 필요'
            track = library.track(manifest['ids'][4])
            if not track['classification']['mood']['protected']:
                library.save_manual(track['id'], {'mood': ['신나는']}, track['revision'])
            manifest['late_manual'] = library.track(track['id'])['classification']
            manifest['checks']['batch_submitted'] = True
        elif args.phase in {'cancel', 'cancel-recheck'}:
            name = 'cancel_fixed' if args.phase == 'cancel-recheck' else 'cancel'
            job = prepare(engine, manifest, name, manifest['ids'][5:], execution='batch',
                          purpose='reclassify' if name == 'cancel_fixed' else 'classify')
            write(output / 'manifest.json', manifest)
            if engine.summary(job)['counts'].get('prepared'):
                engine.submit_batch(job, client)
            if engine.summary(job)['counts'].get('remote'):
                engine.cancel_remote(job, client)
                manifest['checks'][name + '_requested'] = True
        elif args.phase == 'recheck-local':
            class NoGeneration:
                provider = 'anthropic'
                def count_tokens(self, *_):
                    raise AssertionError('완료 작업의 입력 계량 재실행 차단')
                def generate(self, *_):
                    raise AssertionError('완료 작업의 추가 유료 생성 차단')
            with library.connection() as db:
                before = db.execute('SELECT count(*) FROM llm_requests').fetchone()[0]
            revisions = [library.track(identifier)['revision'] for identifier in manifest['ids']]
            for name, expected in (('sync', 3), ('cache', 1)):
                engine.run(manifest['jobs'][name], NoGeneration())
                with library.connection() as db:
                    history = dict(db.execute('SELECT * FROM jobs WHERE id=?', (manifest['jobs'][name],)).fetchone())
                assert history['processed'] == expected and history['failed'] == 0
            with library.connection() as db:
                assert db.execute('SELECT count(*) FROM llm_requests').fetchone()[0] == before
            assert revisions == [library.track(identifier)['revision'] for identifier in manifest['ids']]
            manifest['checks']['updated_version_no_regeneration'] = True
        elif args.phase == 'collect':
            for name in ('batch', 'cancel', 'cancel_fixed'):
                if name in manifest['jobs']:
                    engine.collect_batch(manifest['jobs'][name], client)
            if 'batch' in manifest['jobs']:
                states = engine.summary(manifest['jobs']['batch'])['counts']
                if states.get('remote', 0) == 0:
                    rows = engine.rows(manifest['jobs']['batch'])
                    assert all(row['state'] in {'completed', 'proposal'} for row in rows), 'Batch 응답 검증 실패'
                    late = next(row for row in rows if row['track_id'] == manifest['ids'][4])
                    assert late['state'] == 'proposal' and library.track(manifest['ids'][4])['classification'] == manifest['late_manual']
                    before = engine.job(manifest['jobs']['batch'])
                    with library.connection() as db:
                        before_history = dict(db.execute('SELECT * FROM jobs WHERE id=?', (before['id'],)).fetchone())
                    revisions = [library.track(identifier)['revision'] for identifier in manifest['ids']]
                    engine.collect_batch(manifest['jobs']['batch'], client)
                    after = engine.job(manifest['jobs']['batch'])
                    assert (before['actual'], before['reserved']) == (after['actual'], after['reserved'])
                    with library.connection() as db:
                        history = dict(db.execute('SELECT * FROM jobs WHERE id=?', (before['id'],)).fetchone())
                    assert history['state'] in {'completed', 'partial'} and history['processed'] == 2 and history['failed'] == 0
                    assert before_history['ended_at'] == history['ended_at']
                    assert revisions == [library.track(identifier)['revision'] for identifier in manifest['ids']]
                    manifest['checks'].update(batch_collected=True, late_manual_preserved=True, repeated_collection_unchanged=True)
            if 'cancel' in manifest['jobs']:
                state = engine.summary(manifest['jobs']['cancel'])
                if not state['counts'].get('remote'):
                    manifest['checks']['cancel_settled'] = state['reserved'] == 0
            if 'cancel_fixed' in manifest['jobs']:
                state = engine.summary(manifest['jobs']['cancel_fixed'])
                if not state['counts'].get('remote'):
                    assert state['counts'] == {'cancelled': 1} and state['reserved'] == 0
                    with library.connection() as db:
                        assert db.execute('SELECT state FROM jobs WHERE id=?', (state['id'],)).fetchone()[0] == 'cancelled'
                    manifest['checks']['cancel_fixed_settled'] = True
        manifest.pop('error', None)
    except Exception as error:
        # Keep remote identifiers/reservations, but never dump SDK headers or keys.
        manifest['error'] = dict(type=type(error).__name__, category=getattr(error, 'category', None))
        print(json.dumps(manifest['error']), flush=True)
        raise
    finally:
        client.close()
        write(output / 'manifest.json', manifest)
        report(output, engine, manifest)


if __name__ == '__main__':
    main()
