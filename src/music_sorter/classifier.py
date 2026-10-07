"""Durable, budgeted classification. Preparing a job never sends a request."""
from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from .classification import AXES, validate
from .database import encode, now
from .llm import ProviderError, cache_key, cost_micro, input_bound, parse_tracks, price, track_input
from .tag_io import digest


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

    def prepare(self, ids, *, provider, model, budget, purpose='classify', execution='sync',
                include_lyrics=False, workspace='', external=None, control=None, progress=None):
        if purpose not in PURPOSES or execution not in {'sync', 'batch'}:
            raise ValueError('실행 목적·방식을 확인하세요.')
        if include_lyrics and purpose != 'escalate':
            raise ValueError('가사는 미확정 재판정 단계에서만 사용할 수 있습니다.')
        pricing = price(provider, model, execution)
        budget = budget_micro(budget)
        options = dict(provider=provider, model=model, pricing=pricing, purpose=purpose,
                       execution=execution, include_lyrics=include_lyrics, workspace=workspace, max_tokens_per_track=1000)
        job_id = self.library.start_job('llm')
        self.library.job_state(job_id, 'prepared', '입력·예산 계획만 준비됨. 유료 제출 전입니다.')
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
        rows = self.rows(job_id, 'prepared', 20)
        if rows:
            bound = cost_micro(item['options']['pricing'], input_bound([json.loads(row['input']) for row in rows]), len(rows) * 1000)
            estimate = (sum(counts.values()) + len(rows) - 1) // len(rows) * bound
        item['reservation_estimate'] = estimate
        return item

    def increase_budget(self, job_id, value):
        amount = budget_micro(value)
        with self.library.connection(write=True) as db:
            row = db.execute('SELECT actual,reserved FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
            if not row or amount < row['actual'] + row['reserved']:
                raise ValueError('집계 비용과 미완료 예약보다 낮은 예산은 설정할 수 없습니다.')
            db.execute('UPDATE llm_jobs SET budget=? WHERE id=?', (amount, job_id))

    def _reserve(self, job_id, rows, amount):
        request_id = uuid4().hex
        ids = [row['track_id'] for row in rows]
        with self.library.connection(write=True) as db:
            job = db.execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
            if job['actual'] + job['reserved'] + amount > job['budget']:
                return None
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

    def _settle(self, request_id, response):
        with self.library.connection(write=True) as db:
            request = db.execute('SELECT * FROM llm_requests WHERE id=?', (request_id,)).fetchone()
            if request['state'] in {'received', 'completed'}:
                return
            job = db.execute('SELECT * FROM llm_jobs WHERE id=?', (request['job_id'],)).fetchone()
            usage = response['usage']
            amount = cost_micro(json.loads(job['options'])['pricing'], usage['input'], usage['output'], usage['cached'], usage['cache_write'])
            db.execute("UPDATE llm_requests SET state='received',actual=?,response=?,remote_id=? WHERE id=?",
                       (amount, encode(response), response.get('id'), request_id))
            db.execute('UPDATE llm_jobs SET reserved=reserved-?,actual=actual+? WHERE id=?', (request['reserved'], amount, request['job_id']))
            db.executemany("UPDATE llm_targets SET state='received' WHERE job_id=? AND track_id=?", [(request['job_id'], i) for i in json.loads(request['target_ids'])])

    def _fail(self, request_id, category):
        with self.library.connection(write=True) as db:
            row = db.execute('SELECT * FROM llm_requests WHERE id=?', (request_id,)).fetchone()
            state = 'unknown' if category == 'unknown' else 'failed'
            db.execute('UPDATE llm_requests SET state=?,reason=? WHERE id=?', (state, str(ProviderError(category)), request_id))
            if state != 'unknown':
                db.execute('UPDATE llm_jobs SET reserved=reserved-? WHERE id=?', (row['reserved'], row['job_id']))
            db.executemany('UPDATE llm_targets SET state=?,reason=? WHERE job_id=? AND track_id=?',
                           [(state, str(ProviderError(category)), row['job_id'], i) for i in json.loads(row['target_ids'])])

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
        with self.library.connection() as db:
            requests = [dict(row) for row in db.execute("SELECT * FROM llm_requests WHERE job_id=? AND state='received'", (job_id,))]
        for request in requests:
            response, ids = json.loads(request['response']), json.loads(request['target_ids'])
            try:
                if not response['completed']:
                    raise ValueError('출력 제한·거부 또는 미완료 응답')
                results, errors = parse_tracks(response['text'], ids)
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
        with self.library.connection(write=True) as db:
            rows = db.execute("SELECT track_id FROM llm_targets WHERE job_id=? AND state='failed'", (job_id,)).fetchall()
            count = 0
            for row in rows:
                attempts = db.execute('SELECT coalesce(sum(attempts),0) FROM llm_requests, json_each(llm_requests.target_ids) WHERE job_id=? AND json_each.value=?',
                                      (job_id, row['track_id'])).fetchone()[0]
                if attempts < 4:
                    db.execute("UPDATE llm_targets SET state='prepared',reason='' WHERE job_id=? AND track_id=?", (job_id, row['track_id']))
                    count += 1
        return count

    def submit_batch(self, job_id, client, control=None, progress=None):
        options = self.job(job_id)['options']
        if options['execution'] != 'batch' or client.provider != options['provider']:
            raise ValueError('Batch 작업의 서비스·방식이 일치하지 않습니다.')
        if price(options['provider'], options['model'], 'batch') != options['pricing']:
            raise ValueError('단가가 변경되었습니다. 새 계획이 필요합니다.')
        requests = []
        while len(requests) < 50 and (rows := self.rows(job_id, 'prepared', 20)):
            try:
                if control:
                    control.checkpoint()
                inputs = [json.loads(row['input']) for row in rows]
                tokens = client.count_tokens(options['model'], inputs)
                if tokens > 190000:
                    raise ValueError('문맥·가격 구간 제한. 더 작은 묶음이 필요합니다.')
            except (InterruptedError, ProviderError):
                break
            amount = cost_micro(options['pricing'], tokens, len(rows) * 1000)
            request_id = self._reserve(job_id, rows, amount)
            if request_id is None:
                break
            requests.append({'id': request_id, 'body': client.body(options['model'], inputs, len(rows) * 1000)})
        if not requests:
            self.library.job_state(job_id, 'paused', '미제출. 대상·입력 계량·예산을 확인하세요.')
            return self.summary(job_id)
        try:
            upload_id = client.upload_batch(requests)
            with self.library.connection(write=True) as db:
                db.executemany('UPDATE llm_requests SET upload_id=? WHERE id=?', [(upload_id, r['id']) for r in requests])
            remote_id = client.submit_batch(requests, upload_id, job_id)
            if not isinstance(remote_id, str) or not remote_id:
                raise ProviderError('unknown')
            with self.library.connection(write=True) as db:
                db.executemany("UPDATE llm_requests SET state='remote',remote_id=?,attempts=attempts+1 WHERE id=?", [(remote_id, r['id']) for r in requests])
                for request in requests:
                    db.execute("UPDATE llm_targets SET state='remote' WHERE job_id=? AND track_id IN (SELECT value FROM json_each((SELECT target_ids FROM llm_requests WHERE id=?)))", (job_id, request['id']))
            self.library.job_state(job_id, 'paused', '원격 Batch 제출됨. 종료 후에도 처리될 수 있습니다. 상태 확인으로 수집하세요.')
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
                if response:
                    self._settle(request_id, response)
                    self.apply_received(job_id)
                else:
                    self._fail(request_id, 'invalid')
            # A completed batch can temporarily lack a result file. Missing usage stays reserved.
            with self.library.connection(write=True) as db:
                db.execute("UPDATE llm_requests SET reason=? WHERE job_id=? AND remote_id=? AND state='remote'",
                           ('종료 상태지만 결과·사용량 미확인. 예약 유지, 다음 상태 확인에서 다시 수집합니다.', job_id, remote_id))
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
        if options['execution'] != 'sync':
            raise ValueError('Batch 작업은 원격 제출·결과 확인 절차를 사용하세요.')
        if client.provider != options['provider']:
            raise ValueError('작업 시작 시 API 서비스와 일치하지 않습니다.')
        if price(options['provider'], options['model'], options['execution']) != options['pricing']:
            raise ValueError('단가가 변경되었습니다. 새 계획을 검토하세요.')
        self.apply_received(job_id)
        self.library.job_state(job_id, 'running')
        completed = failed = 0
        while rows := self.rows(job_id, 'prepared', 20):
            try:
                if control:
                    control.checkpoint()
            except InterruptedError:
                self.library.job_state(job_id, 'cancelled', '미전송 대상 보존. 이미 받은 결과는 유지했습니다.')
                break
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
                tokens = client.count_tokens(options['model'], inputs)
                if tokens > 190000:
                    raise ValueError('문맥·가격 구간 제한. 더 작은 묶음이 필요합니다.')
            except ProviderError as error:
                self.library.job_state(job_id, 'paused', '입력 계량 실패: ' + str(error))
                break
            reservation = cost_micro(options['pricing'], tokens, len(uncached) * 1000)
            request_id = self._reserve(job_id, uncached, reservation)
            if request_id is None:
                self.library.job_state(job_id, 'paused', '예산 상한. 예산을 변경한 후 명시적으로 재개하세요.')
                break
            response = None
            with self.library.connection() as db:
                previous_attempts = max((db.execute('SELECT coalesce(sum(attempts),0) FROM llm_requests,json_each(llm_requests.target_ids) WHERE job_id=? AND json_each.value=? AND llm_requests.id!=?',
                                                   (job_id, row['track_id'], request_id)).fetchone()[0] for row in uncached), default=0)
            for attempt in range(max(0, 4 - previous_attempts)):
                with self.library.connection(write=True) as db:
                    db.execute('UPDATE llm_requests SET attempts=attempts+1 WHERE id=?', (request_id,))
                try:
                    response = client.generate(options['model'], inputs, len(uncached) * 1000)
                    break
                except ProviderError as error:
                    if error.category == 'retryable' and attempt + previous_attempts < 3:
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
                break
            self._settle(request_id, response)
            self.apply_received(job_id)
            completed += len(uncached)
            if progress:
                progress(completed, failed)
        else:
            summary = self.summary(job_id)
            state = 'partial' if any(summary['counts'].get(x) for x in ('failed', 'unknown', 'proposal', 'remote')) else 'completed'
            self.library.job_state(job_id, state, '분류 응답 처리 완료. 음악 파일 변경 없음.')
        return self.summary(job_id)
