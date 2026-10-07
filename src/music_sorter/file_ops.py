"""Preview, journaled file application, recovery and exact-byte undo."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from .database import Library, encode, now, path_key
from .scanner import ScanControl, read_snapshot
from .tag_io import digest, genre_tag, read_layout, write_replacement


def safe_name(value: str, fallback="음악", max_length=130) -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip().rstrip(". ")
    if not value:
        value = fallback
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", value, re.I):
        value = "_" + value
    return value[:max_length].rstrip(". ")


def inside_root(root: Path, path: Path) -> Path:
    root = root.absolute()
    path = path.absolute()
    try:
        path.relative_to(root)
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("등록한 음악 루트 밖의 경로입니다.") from None
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("정션·심볼릭 링크 경로는 파일 변경을 보류합니다.")
        if part == root:
            break
    if root.is_symlink() or root.is_junction():
        raise ValueError("음악 루트의 링크 배치를 확인하세요.")
    if path.drive.casefold() != root.drive.casefold() or len(str(path).encode('utf-16-le')) // 2 > 240:
        raise ValueError("다른 볼륨 또는 지원 경로 길이를 넘는 변경입니다.")
    return path


def duplicate_policy(library: Library, tolerance=3.0) -> dict:
    policies = {}
    for group in library.duplicate_groups(tolerance):
        token = ":".join(sorted(track["id"] for track in group["tracks"]))
        for track in group["tracks"]:
            policies[track["id"]] = {"state": "pending", "token": token,
                                     "folder": safe_name(f"{track['artist']} - {track['title']}")}
    with library.connection() as db:
        decisions = db.execute("SELECT * FROM duplicate_decisions").fetchall()
    for decision in decisions:
        entries = decision["signature"].split(":")
        valid = []
        for entry in entries:
            track_id, expected = entry.rsplit("@", 1)
            try:
                current = library.track(track_id)
            except (ValueError, KeyError):
                break
            if current["hash"] != expected or current["file_state"] != "ready":
                break
            valid.append(track_id)
        else:
            kept = json.loads(decision["kept_ids"])
            for track_id in valid:
                policies[track_id] = {"state": "discarded" if decision["decision"] == "keep" and track_id not in kept else "kept",
                                      "token": ":".join(sorted(valid)) + decision["decision"] + encode(kept)}
    return policies


class FileOperations:
    def __init__(self, library: Library, root: Path, rollback: Path | None = None, limit_gib=10):
        self.library, self.root = library, root.absolute()
        self.rollback = rollback or library.path.parent / "rollback"
        self.limit_bytes = int(limit_gib * 1024 ** 3)

    def operations(self, job_id=None, limit=None, offset=0):
        with self.library.connection() as db:
            params = [job_id] if job_id else []
            query = "SELECT * FROM file_ops" + (" WHERE job_id=?" if job_id else "") + " ORDER BY created_at,id"
            if limit is not None:
                query += ' LIMIT ? OFFSET ?'
                params += [max(1, int(limit)), max(0, int(offset))]
            rows = db.execute(query, params).fetchall()
        return [{**dict(row), "plan": json.loads(row["plan"]),
                 "result": json.loads(row["result"]) if row["result"] else None} for row in rows]

    def operation_counts(self, job_id):
        with self.library.connection() as db:
            return {row[0]: row[1] for row in db.execute('SELECT state,count(*) FROM file_ops WHERE job_id=? GROUP BY state', (job_id,))}

    def _iter_operations(self, job_id=None):
        offset = 0
        while rows := self.operations(job_id, 200, offset):
            yield from rows
            offset += len(rows)

    def _state(self, op_id, state, result=None, reason=""):
        with self.library.connection(write=True) as db:
            db.execute("UPDATE file_ops SET state=?,result=COALESCE(?,result),reason=?,updated_at=? WHERE id=?",
                       (state, encode(result) if result is not None else None, reason, now(), op_id))

    def preview(self, track_ids, *, organize=False, rename=False, write_genre=False,
                archive_duplicates=False, tolerance=3.0, control=None, progress=None):
        if not any((organize, rename, write_genre)):
            raise ValueError("폴더 정리·이름 변경·장르 기록 중 실행할 항목을 선택하세요.")
        self.library.bind_root(self.root)
        control, progress = control or ScanControl(), progress or (lambda *_: None)
        policies = duplicate_policy(self.library, tolerance)
        track_ids = list(dict.fromkeys(track_ids))
        selected = set(track_ids)
        job_id = self.library.start_job("file_preview")
        reserved = set()
        completed = blocked = 0
        try:
            for track_id in track_ids:
                control.checkpoint()
                track = self.library.track(track_id)
                policy = policies.get(track_id, {})
                plan = {"id": track_id, "path": track["path"], "hash": track["hash"], "revision": track["revision"],
                        "classification": track["classification"], "destination": track["path"], "genre": None,
                        "duplicate": policy, "tolerance": tolerance, "notes": []}
                state, reason = "planned", ""
                try:
                    source = inside_root(self.root, Path(track["path"]))
                    if track["file_state"] != "ready" or not source.is_file() or digest(source) != track["hash"]:
                        raise ValueError("파일 상태·내용이 달라졌습니다. 재스캔하세요.")
                    directory = source.parent
                    if policy.get("state") == "pending":
                        if not archive_duplicates:
                            raise ValueError("중복 후보를 먼저 검토하거나 후보 폴더 보관을 선택하세요.")
                        members = policy['token'].split(':')
                        if not set(members) <= selected:
                            raise ValueError('중복 후보 폴더 보관은 후보 그룹 전체를 선택해야 합니다.')
                        for member_id in members:
                            member = self.library.track(member_id)
                            member_path = inside_root(self.root, Path(member['path']))
                            if member['file_state'] != 'ready' or digest(member_path) != member['hash']:
                                raise ValueError('중복 후보 그룹에 확인 불가능·외부 변경 파일이 있습니다.')
                        directory = self.root / "_중복검토" / policy["folder"] if organize else directory
                    elif organize:
                        folder = "_삭제예정" if policy.get("state") == "discarded" else (
                            "_미분류" if track["review_state"] == "unclassified" else
                            "_미확정" if track["review_state"] == "unresolved" else track["classification"]["major"]["value"])
                        directory = self.root / safe_name(folder)
                    filename = source.name
                    if rename:
                        stem = f"{track['artist'] or '아티스트 미상'} - {track['title']}"
                        for version in track["version"].split("|"):
                            if version and version.casefold() not in stem.casefold():
                                stem += f" ({version})"
                        # Reserve enough UTF-16 path space for a stable ID collision suffix.
                        maximum = min(130, 220 - len(str(directory)))
                        if maximum < 20:
                            raise ValueError("음악 루트·하위 폴더 경로가 너무 깁니다.")
                        filename = safe_name(stem, max_length=maximum) + ".mp3"
                    destination = inside_root(self.root, directory / filename)
                    key = path_key(destination)
                    collision = (destination.exists() and key != path_key(source)) or key in reserved
                    if collision:
                        destination = destination.with_name(safe_name(destination.stem, max_length=120) + f" [{track_id[:8]}].mp3")
                        key = path_key(destination)
                        if destination.exists() and key != path_key(source) or key in reserved:
                            raise ValueError("안정된 파일 ID 이름에도 충돌이 있습니다. 덮어쓰지 않습니다.")
                    reserved.add(key)
                    plan["destination"] = str(destination)
                    if write_genre:
                        if track["review_state"] != "confirmed":
                            plan["notes"].append("미확정·미분류: 기존 ID3 장르 유지")
                        else:
                            try:
                                read_layout(source)
                                genre = track["classification"]["major"]["value"]
                                if track["metadata_json"].get("genre") != genre:
                                    plan["genre"] = genre
                            except ValueError as error:
                                plan["notes"].append(str(error))
                    if destination == source and plan["genre"] is None:
                        state, reason = "unchanged", "변경할 내용 없음"
                except (ValueError, OSError) as error:
                    state, reason = "blocked", str(error) if isinstance(error, ValueError) else "파일 읽기·경로 확인 실패"
                    blocked += 1
                with self.library.connection(write=True) as db:
                    stamp = now()
                    db.execute("INSERT INTO file_ops VALUES (?,?,?,?,?,?,?, ?,?)",
                               (uuid4().hex, job_id, track_id, state, encode(plan), None, reason, stamp, stamp))
                completed += 1
                if completed % 10 == 0:
                    progress(completed, blocked)
            self.library.job_state(job_id, "prepared", f"{completed}개 계획 · {blocked}개 보류")
        except InterruptedError:
            self.library.job_state(job_id, "cancelled", "미리보기 취소. 음악 파일 변경 없음")
        progress(completed, blocked)
        return job_id

    def daily_backup(self):
        folder = self.library.path.parent / "backups"
        destination = folder / f"auto-apply-{now()[:10]}.sqlite3"
        if not destination.exists():
            self.library.backup(destination)
            old = sorted(folder.glob("auto-apply-*.sqlite3"), key=lambda p: p.name, reverse=True)
            for path in old[7:]:
                path.unlink()  # Only program-owned daily DB backups, after verified new backup.

    def _validate_current(self, plan):
        track = self.library.track(plan["id"])
        if any(track[key] != plan[key] for key in ("path", "hash", "revision", "classification")) or track["file_state"] != "ready":
            raise ValueError("미리보기 이후 파일·분류 판정이 달라졌습니다. 새 미리보기가 필요합니다.")
        current = duplicate_policy(self.library, plan["tolerance"]).get(plan["id"], {})
        if current != plan["duplicate"]:
            raise ValueError("중복 검토 결과가 달라졌습니다. 새 미리보기가 필요합니다.")
        if current.get('state') == 'pending':
            for member_id in current['token'].split(':'):
                member = self.library.track(member_id)
                if member['file_state'] != 'ready' or digest(inside_root(self.root, Path(member['path']))) != member['hash']:
                    raise ValueError('중복 후보 그룹의 파일 상태가 달라졌습니다.')
        return track

    def apply_one(self, operation, *, fault=None):
        """Caller owns the single-writer lock. fault is used only for crash injection tests."""
        plan, op_id = operation["plan"], operation["id"]
        source = inside_root(self.root, Path(plan["path"]))
        destination = inside_root(self.root, Path(plan["destination"]))
        self._validate_current(plan)
        if digest(source) != plan["hash"]:
            raise ValueError("미리보기 이후 원본 내용이 달라졌습니다.")
        if path_key(source) != path_key(destination) and destination.exists():
            raise ValueError("목적지에 파일이 있습니다. 덮어쓰지 않습니다.")
        with self.library.connection() as db:
            busy = db.execute("SELECT id FROM file_ops WHERE track_id=? AND state IN ('prepared','tag_done','file_done','undo_prepared') AND id!=?",
                              (plan["id"], op_id)).fetchone()
        if busy:
            raise ValueError("같은 파일의 이전 작업 상태를 먼저 복구하세요.")
        result = {"hash": plan["hash"], "tag_offset": 0, "original_tag": None, "original_tag_hash": None,
                  "temporary": None, "undo_state": None}
        temporary = None
        if plan["genre"]:
            layout = read_layout(source)
            storage = self.rollback / plan["id"] / op_id
            usage = sum(p.stat().st_size for p in self.rollback.rglob("*.id3")) if self.rollback.exists() else 0
            if usage + len(layout.raw) > self.limit_bytes:
                raise ValueError("ID3 복구 자료 보관 한도를 넘습니다. 보관 한도를 늘린 뒤 다시 적용하세요.")
            storage.mkdir(parents=True, exist_ok=False)
            original = storage / "original.id3"
            with original.open("xb") as stream:
                stream.write(layout.raw)
                stream.flush()
                os.fsync(stream.fileno())
            replacement = genre_tag(layout, plan["genre"])
            temporary = source.with_name(f".music-sorter-{op_id}.tmp")
            result.update(original_tag=str(original), original_tag_hash=digest(original), tag_offset=len(replacement),
                          temporary=str(temporary), original_offset=layout.offset,
                          body_hash=digest(source, layout.offset), warning=usage + len(layout.raw) >= self.limit_bytes * .8)
            write_replacement(source, temporary, layout.offset, replacement)
            reread = read_layout(temporary)
            if [raw for kind, raw in reread.frames if kind != b"TCON"] != [raw for kind, raw in layout.frames if kind != b"TCON"]:
                raise ValueError("장르 외 ID3 프레임 보존 검증에 실패했습니다.")
            result["hash"] = digest(temporary)
        self._state(op_id, "prepared", result)
        if fault:
            fault("prepared")
        if temporary:
            if digest(source) != plan["hash"]:
                raise ValueError("태그 기록 직전 원본이 바뀌었습니다. 임시본과 복구 자료를 보존했습니다.")
            os.replace(temporary, source)
            self._state(op_id, "tag_done", result)
            if fault:
                fault("tag_done")
        destination.parent.mkdir(parents=True, exist_ok=True)
        inside_root(self.root, destination)
        if path_key(source) != path_key(destination):
            if destination.exists():
                raise ValueError("이동 직전 목적지 충돌을 확인했습니다. 덮어쓰지 않습니다.")
            os.rename(source, destination)  # Windows rejects existing destinations.
        if digest(destination) != result["hash"]:
            raise ValueError("파일 적용 결과 해시가 맞지 않습니다. 복구 확인이 필요합니다.")
        self._state(op_id, "file_done", result)
        if fault:
            fault("file_done")
        self._record(operation, result, destination)
        if fault:
            fault("recorded")

    def _record(self, operation, result, destination, undo=False):
        plan = operation["plan"]
        snapshot = read_snapshot(destination)
        expected_hash = plan["hash"] if undo else result["hash"]
        if snapshot["hash"] != expected_hash:
            raise ValueError("기록 직전 파일 내용이 달라졌습니다.")
        with self.library.connection(write=True) as db:
            current = db.execute("SELECT * FROM tracks WHERE id=?", (plan["id"],)).fetchone()
            expected_current = result["hash"] if undo else plan["hash"]
            if current["hash"] != expected_current:
                raise ValueError("DB 파일 기록이 달라졌습니다. 복구 검토가 필요합니다.")
            db.execute("UPDATE tracks SET path=?,path_key=?,hash=?,size=?,mtime_ns=?,metadata_json=?,observed_json=NULL,updated_at=? WHERE id=?",
                       (str(destination), path_key(destination), snapshot["hash"], snapshot["size"], snapshot["mtime_ns"], encode(snapshot), now(), plan["id"]))
            # Program-owned tag edits must not invalidate a user's duplicate decision.
            old_token, new_token = f"{plan['id']}@{expected_current}", f"{plan['id']}@{expected_hash}"
            for row in db.execute("SELECT * FROM duplicate_decisions WHERE signature LIKE ?", (f"%{old_token}%",)).fetchall():
                signature = row["signature"].replace(old_token, new_token)
                db.execute("DELETE FROM duplicate_decisions WHERE signature=?", (row["signature"],))
                db.execute("INSERT OR REPLACE INTO duplicate_decisions VALUES (?,?,?,?)",
                           (signature, row["decision"], row["kept_ids"], row["created_at"]))
            db.execute("UPDATE playlist_outputs SET dirty=1")
            db.execute("UPDATE file_ops SET state=?,reason='',updated_at=? WHERE id=?",
                       ("undone" if undo else "recorded", now(), operation["id"]))

    def apply(self, job_id, control=None, progress=None, fault=None):
        control, progress = control or ScanControl(), progress or (lambda *_: None)
        counts = self.operation_counts(job_id)
        if not counts:
            raise ValueError("적용할 미리보기가 없습니다.")
        self.daily_backup()
        completed = 0
        blocked = counts.get('blocked', 0)
        self.library.job_state(job_id, "running", "파일 적용")
        for operation in self._iter_operations(job_id):
            if operation["state"] != "planned":
                continue
            try:
                control.checkpoint()
                with self.library._write_lock:
                    self.apply_one(operation, fault=fault)
                completed += 1
            except InterruptedError:
                self.library.job_state(job_id, "cancelled", "완료 파일 유지. 남은 계획은 별도 재개 가능")
                return {"completed": completed, "blocked": blocked, "cancelled": True}
            except (ValueError, OSError):
                # Preserve prepared journal stages: failure after filesystem mutation is not a skipped operation.
                with self.library.connection() as db:
                    current = db.execute('SELECT state FROM file_ops WHERE id=?', (operation['id'],)).fetchone()
                if current["state"] == "planned":
                    self._state(operation["id"], "blocked", reason="조건 변경·충돌·파일 접근 실패. 새 미리보기로 확인하세요.")
                blocked += 1
            progress(completed, blocked)
            with self.library.connection(write=True) as db:
                db.execute('UPDATE jobs SET processed=?,failed=? WHERE id=?', (completed, blocked, job_id))
        self.library.job_state(job_id, "partial" if blocked else "completed", f"적용 {completed} · 보류 {blocked}")
        return {"completed": completed, "blocked": blocked}

    def resume(self, job_id):
        """Explicitly finish a journaled intermediate operation; never resubmit a tag edit."""
        self.recover()
        completed = blocked = 0
        for operation in self._iter_operations(job_id):
            if operation['state'] == 'undo_prepared':
                try:
                    with self.library._write_lock:
                        self._undo_one(operation)
                        completed += 1
                except (ValueError, OSError):
                    blocked += 1
                continue
            if operation['state'] not in {'prepared', 'tag_done', 'file_done'}:
                continue
            try:
                with self.library._write_lock:
                    plan, result = operation['plan'], operation['result']
                    self._validate_current(plan)
                    source = inside_root(self.root, Path(plan['path']))
                    destination = inside_root(self.root, Path(plan['destination']))
                    if path_key(source) != path_key(destination) and destination.exists():
                        raise ValueError('목적지 충돌')
                    current_hash = digest(source)
                    if current_hash == plan['hash'] and result['hash'] != plan['hash']:
                        temporary = Path(result['temporary'])
                        inside_root(self.root, temporary)
                        if digest(temporary) != result['hash']:
                            raise ValueError('임시 파일 무결성 오류')
                        if digest(source) != plan['hash']:
                            raise ValueError('원본 외부 변경')
                        os.replace(temporary, source)
                    elif current_hash != result['hash']:
                        raise ValueError('파일 외부 변경')
                    self._state(operation['id'], 'tag_done', result)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    inside_root(self.root, destination)
                    if path_key(source) != path_key(destination):
                        if destination.exists():
                            raise ValueError('목적지 충돌')
                        os.rename(source, destination)
                    if digest(destination) != result['hash']:
                        raise ValueError('최종 파일 무결성 오류')
                    self._state(operation['id'], 'file_done', result)
                    self._record(operation, result, destination)
                    completed += 1
            except (ValueError, OSError):
                blocked += 1
        return {'completed': completed, 'blocked': blocked}

    def recover(self):
        recovered, pending = 0, []
        for operation in self._iter_operations():
            if operation["state"] not in {"prepared", "tag_done", "file_done", "undo_prepared"}:
                continue
            plan, result = operation["plan"], operation["result"]
            source, destination = Path(plan["path"]), Path(plan["destination"])
            try:
                with self.library._write_lock:
                    if operation["state"] == "undo_prepared":
                        target = inside_root(self.root, source)
                        if target.exists() and digest(target) == plan["hash"] and (path_key(source) == path_key(destination) or not destination.exists()):
                            self._record(operation, result, source, undo=True)
                            recovered += 1
                        else:
                            pending.append(operation["id"])
                    else:
                        target = inside_root(self.root, destination)
                        if target.exists() and digest(target) == result["hash"] and (path_key(source) == path_key(destination) or not source.exists()):
                            self._record(operation, result, destination)
                            recovered += 1
                        else:
                            pending.append(operation["id"])
            except (OSError, ValueError):
                pending.append(operation["id"])
        return {"recovered": recovered, "pending": pending}

    def undo_preview(self, operation_id):
        with self.library.connection() as db:
            row = db.execute('SELECT job_id FROM file_ops WHERE id=?', (operation_id,)).fetchone()
        matches = [op for op in self._iter_operations(row[0]) if op['id'] == operation_id] if row else []
        if not matches or matches[0]["state"] != "recorded":
            raise ValueError("되돌릴 완료 파일 작업이 없습니다.")
        operation = matches[0]
        later = [op for op in self._iter_operations() if op["track_id"] == operation["track_id"] and op["state"] == "recorded"
                 and (op["created_at"], op["id"]) > (operation["created_at"], operation["id"])]
        return sorted(later + [operation], key=lambda op: (op["created_at"], op["id"]), reverse=True)

    def undo(self, operation_id, fault=None):
        for operation in self.undo_preview(operation_id):
            with self.library._write_lock:
                self._undo_one(operation, fault)

    def _undo_one(self, operation, fault=None):
        plan, result = operation['plan'], operation['result']
        current = inside_root(self.root, Path(plan['destination']))
        original = inside_root(self.root, Path(plan['path']))
        track = self.library.track(plan['id'])
        current_hash = digest(current)
        expected = {result['hash'], plan['hash']} if operation['state'] == 'undo_prepared' else {result['hash']}
        if path_key(track['path']) != path_key(current) or track['hash'] != result['hash'] or current_hash not in expected:
            raise ValueError('적용 후 외부 변경·누락이 있어 되돌리기를 보류합니다.')
        if path_key(current) != path_key(original) and original.exists():
            raise ValueError('원래 위치에 다른 파일이 있습니다. 덮어쓰지 않습니다.')
        temporary = None
        if result['original_tag'] and current_hash != plan['hash']:
            backup = Path(result['original_tag'])
            if digest(backup) != result['original_tag_hash']:
                raise ValueError('ID3 복구 자료 무결성을 확인할 수 없습니다.')
            temporary = current.with_name(f".music-sorter-undo-{operation['id']}.tmp")
            if not temporary.exists():
                write_replacement(current, temporary, result['tag_offset'], backup.read_bytes())
            if digest(temporary) != plan['hash']:
                raise ValueError('원본 복원 해시가 맞지 않습니다. 원본을 교체하지 않았습니다.')
        self._state(operation['id'], 'undo_prepared', result)
        if fault:
            fault('undo_prepared')
        if temporary:
            if digest(current) != result['hash']:
                raise ValueError('되돌리기 직전 외부 변경을 확인했습니다.')
            os.replace(temporary, current)
        if fault:
            fault('undo_tag_done')
        original.parent.mkdir(parents=True, exist_ok=True)
        inside_root(self.root, original)
        if path_key(original) != path_key(current):
            if original.exists():
                raise ValueError('되돌리기 직전 원래 위치 충돌을 확인했습니다.')
            os.rename(current, original)
        if fault:
            fault('undo_file_done')
        if digest(original) != plan['hash']:
            raise ValueError('원본 복원 결과 해시가 맞지 않습니다.')
        self._record(operation, result, original, undo=True)
