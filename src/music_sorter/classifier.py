"""Durable classification with usage records. Preparing never sends a request."""
from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from .classification import AXES, validate
from .database import encode, now
from .llm import PROMPT_VERSION, SYSTEM, ProviderError, cache_key, cost_micro, input_bound, parse_tracks, price, response_schema, track_input
from .classification import TAXONOMY
from .tag_io import digest
from .llm import output_limit, reservation_cost, usage_cost


PURPOSES = {'classify', 'escalate', 'reclassify'}
ACTIVE = ('prepared', 'sending', 'unknown', 'remote', 'received')


def budget_micro(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > 100000 or amount.as_tuple().exponent < -6:
            raise ValueError
        return int(amount * 1000000)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('USD 예산을 0보다 큰 금액으로 입력하세요 (최대 소수 6자리).') from None


def snapshot(track):
    return {key: track[key] for key in ('id', 'hash', 'path', 'revision', 'file_state', 'classification')}


def merge_result(current, proposal, purpose):
    updated, conflicts = deepcopy(current), []
    for axis in AXES:
        if current[axis]['protected']:
            if current[axis]['value'] != proposal[axis]['value'] or current[axis]['status'] != proposal[axis]['status']:
                conflicts.append(axis)
            continue
        if purpose == 'escalate' and current[axis]['status'] == 'confirmed':
            continue
        updated[axis] = deepcopy(proposal[axis])
    if current['major']['protected'] and current['major']['value'] != proposal['major']['value']:
        updated['subgenre'] = deepcopy(current['subgenre'])
        conflicts.append('subgenre')
    try:
        validate(updated)
    except ValueError:
        # A protected major/genre pair cannot be made inconsistent by the other axis.
        updated['major'], updated['subgenre'] = deepcopy(current['major']), deepcopy(current['subgenre'])
        conflicts.extend(('major', 'subgenre'))
        validate(updated)
    return updated, sorted(set(conflicts))


class Classifier:
    def __init__(self, library):
        self.library = library

    def prepare(self, ids, *, provider, model, budget=None, purpose='classify', execution='sync',
                include_lyrics=False, workspace='', external=None, control=None, progress=None,
                tracks_per_request=20, max_output_tokens_per_track=1000, timeout_seconds=60, max_retries=3):
        if purpose not in PURPOSES or execution not in {'sync', 'batch'}:
            raise ValueError('실행 목적·방식을 확인하세요.')
        if include_lyrics and purpose != 'escalate':
            raise ValueError('가사는 미확정 재판정 단계에서만 사용할 수 있습니다.')
        pricing = price(provider, model, execution)
        # Legacy callers may pass budget; it is no longer a submission limit.
        budget = 0
        for value, low, high in [(tracks_per_request, 1, 20), (max_output_tokens_per_track, 256, 2000), (timeout_seconds, 10, 180), (max_retries, 0, 3)]:
            if type(value) is not int or not low <= value <= high:
                raise ValueError('요청 묶음·출력 제한·시간·재시도 범위를 확인하세요.')
        options = dict(provider=provider, model=model, pricing=pricing, purpose=purpose,
                       execution=execution, include_lyrics=include_lyrics, workspace=workspace, max_tokens_per_track=max_output_tokens_per_track,
                       tracks_per_request=tracks_per_request, timeout_seconds=timeout_seconds, max_retries=max_retries)
        from .catalog import active_catalog
        options['contract'] = dict(prompt_version=PROMPT_VERSION, system=SYSTEM, taxonomy=active_catalog(), schema=response_schema(), prompt_cache=provider == 'anthropic')
        job_id = self.library.start_job('llm')
        self.library.job_state(job_id, 'prepared', '분류 입력 준비됨. 아직 제출하지 않았습니다.')
        with self.library.connection(write=True) as db:
            db.execute('INSERT INTO llm_jobs(id,options,budget) VALUES (?,?,?)', (job_id, encode(options), budget))
        count, blocked = 0, 0
        for track_id in dict.fromkeys(ids):
            try:
                if control:
                    control.checkpoint()
            except InterruptedError:
                self.library.job_state(job_id, 'cancelled', '준비 취소. API 요청 없음.')
                break
            track = self.library.track(track_id)
            with self.library.connection() as db:
                active = db.execute('SELECT 1 FROM llm_targets WHERE track_id=? AND state IN (?,?,?,?,?) LIMIT 1',
                                    (track_id, *ACTIVE)).fetchone()
                failed = db.execute("SELECT 1 FROM llm_targets WHERE track_id=? AND state='failed' LIMIT 1", (track_id,)).fetchone() if purpose == 'classify' else None
            eligible = track['file_state'] == 'ready' and not active and not all(x['protected'] for x in track['classification'].values())
            eligible &= not failed
            eligible &= purpose == 'reclassify' or track['review_state'] == ('unclassified' if purpose == 'classify' else 'unresolved')
            if not eligible:
                blocked += 1
                continue
            try:
                if digest(Path(track['path'])) != track['hash']:
                    raise ValueError('외부 변경')
                evidence = external(track) if callable(external) else external.get(track_id) if external else None
                inputs = track_input(track, evidence, include_lyrics and track['metadata_json'].get('has_lyrics', False))
            except (OSError, ValueError):
                blocked += 1
                continue
            inputs['id'] = track_id
            if control:
                control.report(f'분류 준비 중 · {count + 1:,}번째 곡 · {inputs["title"]} — {inputs["artist"]}')
            key = cache_key(provider, model, {'input': inputs, 'file_hash': track['hash']}, purpose)
            with self.library.connection(write=True) as db:
                if db.execute('SELECT 1 FROM llm_targets WHERE track_id=? AND state IN (?,?,?,?,?) LIMIT 1', (track_id, *ACTIVE)).fetchone():
                    blocked += 1
                    continue
                db.execute('INSERT INTO llm_targets(job_id,track_id,snapshot,input,cache_key,state) VALUES (?,?,?,?,?,?)',
                           (job_id, track_id, encode(snapshot(track)), encode(inputs), key, 'prepared'))
            count += 1
            if progress:
                progress(count, blocked)
        return job_id

    def job(self, job_id):
        with self.library.connection() as db:
            row = db.execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('분류 작업을 찾을 수 없습니다.')
        item = dict(row)
        item['options'] = json.loads(item['options'])
        return item

    def rows(self, job_id, state=None, limit=200, offset=0):
        with self.library.connection() as db:
            query, params = 'SELECT * FROM llm_targets WHERE job_id=?', [job_id]
            if state:
                query += ' AND state=?'
                params.append(state)
            rows = db.execute(query + ' ORDER BY sequence LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
        return [dict(row) for row in rows]

    def summary(self, job_id):
        item = self.job(job_id)
        with self.library.connection() as db:
            counts = {row[0]: row[1] for row in db.execute('SELECT state,count(*) FROM llm_targets WHERE job_id=? GROUP BY state', (job_id,))}
        item['counts'] = counts
        estimate = 0
        rows = self.rows(job_id, 'prepared', item['options'].get('tracks_per_request', 20))
        if rows:
            bound = reservation_cost(item['options'], input_bound([json.loads(row['input']) for row in rows]), output_limit(item['options'], len(rows)))
            estimate = (sum(counts.values()) + len(rows) - 1) // len(rows) * bound
        item['reservation_estimate'] = estimate
        return item

    def _reserve(self, job_id, rows, amount):
        request_id = uuid4().hex
        ids = [row['track_id'] for row in rows]
        with self.library.connection(write=True) as db:
            # Global per-library active-target check and reservation happen under one write lock.
            for row in rows:
                target = db.execute('SELECT state FROM llm_targets WHERE sequence=?', (row['sequence'],)).fetchone()
                if not target or target[0] != 'prepared':
                    raise ValueError('분류 대상 상태가 변경되었습니다.')
            db.execute('INSERT INTO llm_requests(id,job_id,target_ids,state,reserved,created_at) VALUES (?,?,?,?,?,?)',
                       (request_id, job_id, encode(ids), 'sending', amount, now()))
            db.execute('UPDATE llm_jobs SET reserved=reserved+? WHERE id=?', (amount, job_id))
            db.executemany("UPDATE llm_targets SET state='sending' WHERE job_id=? AND track_id=?", [(job_id, i) for i in ids])
        return request_id

    def cancel_prepared(self, job_id):
        with self.library.connection(write=True) as db:
            count = db.execute("UPDATE llm_targets SET state='cancelled',reason=? WHERE job_id=? AND state='prepared'",
                               ('사용자가 미제출 계획을 취소했습니다. API 요청 없음.', job_id)).rowcount
        self.library.job_state(job_id, 'cancelled', f'미제출 {count}곡 취소. 기존 원격·불확실 요청과 예약은 유지합니다.')
        return count

    def uncertain_requests(self, job_id):
        with self.library.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,remote_id,reserved,reason,created_at FROM llm_requests WHERE job_id=? AND state='unknown' ORDER BY created_at LIMIT 200", (job_id,))]

    def confirm_unprocessed(self, job_id, request_ids, reason, *, confirmed=False):
        if confirmed is not True or not request_ids or not reason.strip() or len(reason) > 600:
            raise ValueError('제공자의 미처리·미과금 확인과 근거가 필요합니다.')
        stamp = now()
        count = released = 0
        with self.library.connection(write=True) as db:
            for request_id in dict.fromkeys(request_ids):
                row = db.execute("SELECT * FROM llm_requests WHERE job_id=? AND id=? AND state='unknown'", (job_id, request_id)).fetchone()
                if not row:
                    raise ValueError('요청 상태가 바뀌었습니다. 다시 확인하세요.')
                note = '사용자가 제공자 미처리·미과금을 확인함 · ' + stamp + ' · ' + reason.strip()
                db.execute("UPDATE llm_requests SET state='cancelled',reason=? WHERE id=?", (note, request_id))
                db.executemany("UPDATE llm_targets SET state='cancelled',reason=? WHERE job_id=? AND track_id=? AND state='unknown'", [(note, job_id, i) for i in json.loads(row['target_ids'])])
                released += row['reserved']
                count += 1
            db.execute('UPDATE llm_jobs SET reserved=reserved-? WHERE id=? AND reserved>=?', (released, job_id, released))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                raise ValueError('예약 금액이 맞지 않습니다. 데이터를 확인하세요.')
        self.library.job_state(job_id, 'paused', f'제공자 미처리·미과금 확인 {count}요청. 자동 재전송 없음. 새 계획으로 실행하세요.')
        return dict(requests=count, released=released)

    def _ready_rows(self, job_id, rows):
        usable = []
        for target in rows:
            try:
                current = self.library.track(target['track_id'])
                old = json.loads(target['snapshot'])
                valid = snapshot(current) == old and current['file_state'] == 'ready' and digest(Path(current['path'])) == old['hash']
            except (OSError, ValueError):
                valid = False
            if valid:
                usable.append(target)
            else:
                with self.library.connection(write=True) as db:
                    db.execute("UPDATE llm_targets SET state='blocked',reason=? WHERE sequence=? AND state='prepared'",
                               ('제출 전 파일·판정 변경. 새 스캔·계획이 필요합니다. 유료 요청 없음.', target['sequence']))
        return usable

    def _settle(self, request_id, response):
        with self.library.connection(write=True) as db:
            request = db.execute('SELECT * FROM llm_requests WHERE id=?', (request_id,)).fetchone()
            if request['state'] in {'received', 'completed'}:
                return
            job = db.execute('SELECT * FROM llm_jobs WHERE id=?', (request['job_id'],)).fetchone()
            usage = response['usage']
            amount = usage_cost(json.loads(job['options']), usage)
            db.execute("UPDATE llm_requests SET state='received',actual=?,response=?,remote_id=? WHERE id=?",
                       (amount, encode(response), response.get('id'), request_id))
            db.execute('UPDATE llm_jobs SET reserved=reserved-?,actual=actual+? WHERE id=?', (request['reserved'], amount, request['job_id']))
            db.executemany("UPDATE llm_targets SET state='received' WHERE job_id=? AND track_id=?", [(request['job_id'], i) for i in json.loads(request['target_ids'])])

    def _fail(self, request_id, category):
        with self.library.connection(write=True) as db:
            row = db.execute('SELECT * FROM llm_requests WHERE id=?', (request_id,)).fetchone()
            state = 'unknown' if category == 'unknown' else 'cancelled' if category == 'cancelled' else 'failed'
            reason = {'cancelled': '원격 Batch에서 미처리 취소가 확인되었습니다. 비용 예약을 해제했습니다.',
                      'expired': '원격 Batch의 처리 시간이 만료되었습니다. 미처리 요청의 비용 예약을 해제했습니다.'}.get(category, str(ProviderError(category)))
            db.execute('UPDATE llm_requests SET state=?,reason=? WHERE id=?', (state, reason, request_id))
            if state != 'unknown':
                db.execute('UPDATE llm_jobs SET reserved=reserved-? WHERE id=?', (row['reserved'], row['job_id']))
            db.executemany('UPDATE llm_targets SET state=?,reason=? WHERE job_id=? AND track_id=?',
                           [(state, reason, row['job_id'], i) for i in json.loads(row['target_ids'])])

    def _record_progress(self, job_id):
        with self.library.connection(write=True) as db:
            counts = {row[0]: row[1] for row in db.execute('SELECT state,count(*) FROM llm_targets WHERE job_id=? GROUP BY state', (job_id,))}
            db.execute('UPDATE jobs SET processed=?,failed=? WHERE id=?',
                       (counts.get('completed', 0) + counts.get('proposal', 0),
                        counts.get('failed', 0) + counts.get('blocked', 0), job_id))
        return counts

    def _apply_one(self, job_id, track_id, result):
        with self.library.connection() as db:
            row = db.execute('SELECT * FROM llm_targets WHERE job_id=? AND track_id=?', (job_id, track_id)).fetchone()
        target = dict(row) if row else None
        if not target or target['state'] not in {'received', 'prepared'}:
            return
        proposed, old = result['classification'], json.loads(target['snapshot'])
        options = self.job(job_id)['options']
        reason, state = '', 'completed'
        try:
            current = self.library.track(track_id)
            valid = snapshot(current) == old and digest(Path(current['path'])) == old['hash']
        except (OSError, ValueError):
            valid = False
        with self.library.connection(write=True) as db:
            row = db.execute('SELECT * FROM tracks WHERE id=?', (track_id,)).fetchone()
            # Recheck DB in the commit transaction after the full file read.
            if not row or not valid or snapshot(self.library._decode(row)) != old:
                state, reason = 'proposal', '입력·파일·분류가 변경됨. 자동 반영하지 않고 제안을 보존했습니다.'
            else:
                original = json.loads(row['classification'])
                updated, conflicts = merge_result(original, proposed, options['purpose'])
                if updated != original:
                    self.library._save_classification(db, row, original, updated, 'llm:' + job_id)
                if conflicts or result.get('suggested_tags'):
                    state, reason = 'proposal', '수동 보호/대분류-세부 장르 충돌 또는 목록 밖 제안 태그를 검토하세요.'
            db.execute('UPDATE llm_targets SET state=?,result=?,reason=? WHERE job_id=? AND track_id=?',
                       (state, encode(result), reason, job_id, track_id))
            db.execute('INSERT OR REPLACE INTO llm_cache VALUES (?,?,?)', (target['cache_key'], encode(result), now()))

    def apply_received(self, job_id):
        options = self.job(job_id)['options']
        with self.library.connection() as db:
            requests = [dict(row) for row in db.execute("SELECT * FROM llm_requests WHERE job_id=? AND state='received'", (job_id,))]
        for request in requests:
            response, ids = json.loads(request['response']), json.loads(request['target_ids'])
            try:
                if not response['completed']:
                    raise ValueError('출력 제한·거부 또는 미완료 응답')
                results, errors = parse_tracks(response['text'], ids, options.get('contract', {}).get('taxonomy'))
            except ValueError:
                results, errors = {}, {i: '출력 제한·거부 또는 응답 전체 검증 실패' for i in ids}
            for track_id, result in results.items():
                self._apply_one(job_id, track_id, result)
            with self.library.connection(write=True) as db:
                for track_id, reason in errors.items():
                    db.execute("UPDATE llm_targets SET state='failed',reason=? WHERE job_id=? AND track_id=? AND state='received'", (reason, job_id, track_id))
                db.execute("UPDATE llm_requests SET state='completed' WHERE id=?", (request['id'],))

    def recover(self):
        with self.library.connection(write=True) as db:
            db.execute("UPDATE llm_requests SET state='unknown',reason=? WHERE state='sending' AND remote_id IS NULL",
                       ('이전 실행 중 전송됨. 처리 여부 확인 전 재전송하지 않습니다.',))
            db.execute("UPDATE llm_targets SET state='unknown',reason=? WHERE state='sending' AND job_id IN (SELECT job_id FROM llm_requests WHERE state='unknown')",
                       ('전송 중 중단. 처리 확인 필요',))
            jobs = [r[0] for r in db.execute("SELECT DISTINCT job_id FROM llm_requests WHERE state='received'")]
        for job_id in jobs:
            self.apply_received(job_id)

    def retry_failed(self, job_id):
        maximum = self.job(job_id)['options'].get('max_retries', 3) + 1
        with self.library.connection(write=True) as db:
            rows = db.execute("SELECT track_id FROM llm_targets WHERE job_id=? AND state='failed'", (job_id,)).fetchall()
            count = 0
            for row in rows:
                attempts = db.execute('SELECT coalesce(sum(attempts),0) FROM llm_requests, json_each(llm_requests.target_ids) WHERE job_id=? AND json_each.value=?',
                                      (job_id, row['track_id'])).fetchone()[0]
                if attempts < maximum:
                    db.execute("UPDATE llm_targets SET state='prepared',reason='' WHERE job_id=? AND track_id=?", (job_id, row['track_id']))
                    count += 1
        return count

    def submit_batch(self, job_id, client, control=None, progress=None):
        options = self.job(job_id)['options']
        client.contract = options.get('contract')
        if options['execution'] != 'batch' or client.provider != options['provider']:
            raise ValueError('Batch 작업의 서비스·방식이 일치하지 않습니다.')
        requests = []
        while len(requests) < 50 and (rows := self.rows(job_id, 'prepared', options.get('tracks_per_request', 20))):
            rows = self._ready_rows(job_id, rows)
            if not rows:
                continue
            try:
                if control:
                    control.checkpoint()
                inputs = [json.loads(row['input']) for row in rows]
                if control:
                    control.report(f'서버 처리 요청 준비 중 · {len(requests) + 1}번째 묶음 · {len(inputs)}곡 입력 확인')
                tokens = client.count_tokens(options['model'], inputs)
                if tokens > 190000:
                    raise ValueError('입력 길이 제한. 더 작은 묶음이 필요합니다.')
            except (InterruptedError, ProviderError):
                break
            amount = reservation_cost(options, tokens, output_limit(options, len(rows)))
            request_id = self._reserve(job_id, rows, amount)
            requests.append({'id': request_id, 'body': client.body(options['model'], inputs, output_limit(options, len(rows)))})
        if not requests:
            self.library.job_state(job_id, 'paused', '미제출. 대상·입력 계량을 확인하세요.')
            return self.summary(job_id)
        try:
            if control:
                control.checkpoint()
                control.report(f'AI 서비스에 전달 중 · {len(requests)}개 묶음 · 서버 응답 대기…')
            upload_id = client.upload_batch(requests)
            with self.library.connection(write=True) as db:
                db.executemany('UPDATE llm_requests SET upload_id=? WHERE id=?', [(upload_id, r['id']) for r in requests])
            if control:
                control.checkpoint()
            remote_id = client.submit_batch(requests, upload_id, job_id)
            if not isinstance(remote_id, str) or not remote_id:
                raise ProviderError('unknown')
            with self.library.connection(write=True) as db:
                db.executemany("UPDATE llm_requests SET state='remote',remote_id=?,attempts=attempts+1 WHERE id=?", [(remote_id, r['id']) for r in requests])
                for request in requests:
                    db.execute("UPDATE llm_targets SET state='remote' WHERE job_id=? AND track_id IN (SELECT value FROM json_each((SELECT target_ids FROM llm_requests WHERE id=?)))", (job_id, request['id']))
            self.library.job_state(job_id, 'paused', '원격 Batch 제출됨. 종료 후에도 처리될 수 있습니다. 상태 확인으로 수집하세요.')
        except InterruptedError:
            for request in requests:
                self._fail(request['id'], 'cancelled')
            self.library.job_state(job_id, 'cancelled', '서버에 제출하기 전에 중단했습니다.')
        except ProviderError as error:
            # An upload failure is not generation, but uncertain creation can have accepted work.
            for request in requests:
                self._fail(request['id'], error.category)
            self.library.job_state(job_id, 'paused', str(error))
        return self.summary(job_id)

    def collect_batch(self, job_id, client, control=None, progress=None):
        if client.provider != self.job(job_id)['options']['provider']:
            raise ValueError('작업의 API 서비스와 일치하지 않습니다.')
        with self.library.connection() as db:
            remotes = [row[0] for row in db.execute("SELECT DISTINCT remote_id FROM llm_requests WHERE job_id=? AND state='remote'", (job_id,))]
        for remote_id in remotes:
            if control:
                control.checkpoint()
                control.report('AI 서비스에서 진행 상황·결과 가져오는 중…')
            status, files = client.batch_status(remote_id)
            if status not in {'ended', 'completed', 'failed', 'expired', 'cancelled'}:
                continue
            seen = set()
            for request_id, response in client.batch_results(remote_id, files):
                if control:
                    control.checkpoint()
                if request_id in seen:
                    raise ValueError('Batch 결과 요청 ID가 중복되었습니다.')
                seen.add(request_id)
                with self.library.connection() as db:
                    request = db.execute('SELECT state FROM llm_requests WHERE id=? AND job_id=? AND remote_id=?', (request_id, job_id, remote_id)).fetchone()
                if not request:
                    raise ValueError('이 작업에 속하지 않는 Batch 결과입니다.')
                if request[0] != 'remote':
                    continue
                if response and 'outcome' in response:
                    category = {'canceled': 'cancelled', 'expired': 'expired', 'errored': 'invalid'}.get(response['outcome'], 'unknown')
                    self._fail(request_id, category)
                elif response:
                    self._settle(request_id, response)
                    self.apply_received(job_id)
                else:
                    self._fail(request_id, 'invalid')
            # A completed batch can temporarily lack a result file. Missing usage stays reserved.
            with self.library.connection(write=True) as db:
                db.execute("UPDATE llm_requests SET reason=? WHERE job_id=? AND remote_id=? AND state='remote'",
                           ('종료 상태지만 결과·사용량 미확인. 예약 유지, 다음 상태 확인에서 다시 수집합니다.', job_id, remote_id))
        counts = self._record_progress(job_id)
        if counts and not any(counts.get(state) for state in ACTIVE):
            state = 'cancelled' if set(counts) == {'cancelled'} else 'partial' if any(counts.get(s) for s in ('failed', 'blocked', 'proposal', 'cancelled')) else 'completed'
            with self.library.connection() as db:
                previous = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
            if previous != state:
                self.library.job_state(job_id, state, 'Batch 응답 수집 완료. 음악 파일 변경 없음.')
        return self.summary(job_id)

    def cancel_remote(self, job_id, client):
        with self.library.connection() as db:
            remotes = [r[0] for r in db.execute("SELECT DISTINCT remote_id FROM llm_requests WHERE job_id=? AND state='remote'", (job_id,))]
        for remote in remotes:
            client.cancel_batch(remote)
        self.library.job_state(job_id, 'paused', '원격 취소 요청됨. 취소 완료·부분 결과·비용은 상태 확인으로 수집하세요.')

    def run(self, job_id, client, control=None, progress=None):
        job = self.job(job_id)
        options = job['options']
        client.contract = options.get('contract')
        if options['execution'] != 'sync':
            raise ValueError('Batch 작업은 원격 제출·결과 확인 절차를 사용하세요.')
        if client.provider != options['provider']:
            raise ValueError('작업 시작 시 API 서비스와 일치하지 않습니다.')
        self.apply_received(job_id)
        self.library.job_state(job_id, 'running')
        completed = failed = 0
        total = sum(self.summary(job_id)['counts'].values())
        while rows := self.rows(job_id, 'prepared', options.get('tracks_per_request', 20)):
            try:
                if control:
                    control.checkpoint()
            except InterruptedError:
                self.library.job_state(job_id, 'cancelled', '미전송 대상 보존. 이미 받은 결과는 유지했습니다.')
                break
            rows = self._ready_rows(job_id, rows)
            if not rows:
                continue
            uncached = []
            for row in rows:
                with self.library.connection() as db:
                    cached = db.execute('SELECT result FROM llm_cache WHERE key=?', (row['cache_key'],)).fetchone()
                if cached:
                    self._apply_one(job_id, row['track_id'], json.loads(cached[0]))
                    completed += 1
                else:
                    uncached.append(row)
            if not uncached:
                continue
            inputs = [json.loads(row['input']) for row in uncached]
            try:
                if control:
                    control.report(f'{total:,}곡 중 {completed:,}곡 처리 · 다음 {len(inputs)}곡 입력 확인 중…')
                tokens = client.count_tokens(options['model'], inputs)
                if tokens > 190000:
                    raise ValueError('입력 길이 제한. 더 작은 묶음이 필요합니다.')
            except ProviderError as error:
                self.library.job_state(job_id, 'paused', '입력 계량 실패: ' + str(error))
                break
            reservation = reservation_cost(options, tokens, output_limit(options, len(uncached)))
            request_id = self._reserve(job_id, uncached, reservation)
            response = None
            with self.library.connection() as db:
                previous_attempts = max((db.execute('SELECT coalesce(sum(attempts),0) FROM llm_requests,json_each(llm_requests.target_ids) WHERE job_id=? AND json_each.value=? AND llm_requests.id!=?',
                                                   (job_id, row['track_id'], request_id)).fetchone()[0] for row in uncached), default=0)
            max_attempts = options.get('max_retries', 3) + 1
            for attempt in range(max(0, max_attempts - previous_attempts)):
                with self.library.connection(write=True) as db:
                    db.execute('UPDATE llm_requests SET attempts=attempts+1 WHERE id=?', (request_id,))
                try:
                    if control:
                        control.checkpoint()
                        control.report(f'{total:,}곡 중 {completed:,}곡 처리 · {len(inputs)}곡 AI 응답 대기…' +
                                       (f' · 재시도 {attempt}' if attempt else ''))
                    response = client.generate(options['model'], inputs, output_limit(options, len(uncached)))
                    break
                except InterruptedError:
                    break
                except ProviderError as error:
                    if error.category == 'retryable' and attempt + previous_attempts < max_attempts - 1:
                        if control and control.cancelled.wait(min(2 ** (attempt + 1), 8)):
                            break
                        continue
                    self._fail(request_id, error.category)
                    self.library.job_state(job_id, 'paused' if error.category in {'auth', 'unknown'} else 'partial', str(error))
                    failed += len(uncached)
                    break
            if response is None:
                # Cancellation during a definitive 429 retry releases an uncharged reservation.
                with self.library.connection() as db:
                    still_sending = db.execute('SELECT state FROM llm_requests WHERE id=?', (request_id,)).fetchone()[0] == 'sending'
                if still_sending:
                    self._fail(request_id, 'retryable')
                if control and control.cancelled.is_set():
                    self.library.job_state(job_id, 'cancelled', '사용자가 중단했습니다. 이미 받은 결과는 유지합니다.')
                break
            self._settle(request_id, response)
            self.apply_received(job_id)
            completed += len(uncached)
            if progress:
                progress(completed, failed)
        else:
            summary = self.summary(job_id)
            state = 'partial' if any(summary['counts'].get(x) for x in ('failed', 'blocked', 'cancelled', 'unknown', 'proposal', 'remote')) else 'completed'
            self.library.job_state(job_id, state, '분류 응답 처리 완료. 음악 파일 변경 없음.')
        self._record_progress(job_id)
        return self.summary(job_id)
