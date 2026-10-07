from __future__ import annotations

import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .classification import AXES, empty_classification, manual_patch, review_state, validate

SCHEMA_VERSION = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def path_key(path: Path | str) -> str:
    return str(Path(path).resolve()).casefold()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Library:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1, 2, SCHEMA_VERSION}:
            raise RuntimeError("지원하지 않는 DB 버전입니다. 새 버전으로 열어 주세요.")
        if version in {1, 2}:
            self.backup(path.parent / "backups" / f"before-schema-3-{uuid4().hex}.sqlite3")
        with self.connection(write=True) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, path_key TEXT UNIQUE NOT NULL,
                    hash TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL,
                    title_key TEXT NOT NULL, artist_key TEXT NOT NULL, version TEXT NOT NULL,
                    duration REAL, bitrate INTEGER, sample_rate INTEGER, grade TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, observed_json TEXT,
                    file_state TEXT NOT NULL DEFAULT 'ready',
                    classification TEXT NOT NULL, review_state TEXT NOT NULL DEFAULT 'unclassified',
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tracks_hash ON tracks(hash);
                CREATE INDEX IF NOT EXISTS tracks_artist_title ON tracks(artist_key,title_key,version);
                CREATE INDEX IF NOT EXISTS tracks_review ON tracks(review_state,file_state);
                CREATE INDEX IF NOT EXISTS tracks_title ON tracks(title_key);
                CREATE TABLE IF NOT EXISTS classification_history (
                    id INTEGER PRIMARY KEY, track_id TEXT NOT NULL, previous TEXT NOT NULL,
                    current TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS observations (
                    job_id TEXT NOT NULL, path_key TEXT NOT NULL, payload TEXT,
                    error TEXT, PRIMARY KEY(job_id,path_key)
                );
                CREATE TABLE IF NOT EXISTS duplicate_decisions (
                    signature TEXT PRIMARY KEY, decision TEXT NOT NULL,
                    kept_ids TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_links (
                    id INTEGER PRIMARY KEY, old_id TEXT, new_id TEXT NOT NULL,
                    decision TEXT NOT NULL, inherit INTEGER NOT NULL,
                    previous TEXT NOT NULL, current TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bulk_targets (
                    sequence INTEGER PRIMARY KEY, job_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, snapshot TEXT NOT NULL, proposed TEXT,
                    state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                    UNIQUE(job_id,track_id)
                );
                CREATE INDEX IF NOT EXISTS bulk_job_sequence ON bulk_targets(job_id,sequence);
                CREATE INDEX IF NOT EXISTS bulk_job_state_sequence ON bulk_targets(job_id,state,sequence);
                CREATE TABLE IF NOT EXISTS file_ops (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    state TEXT NOT NULL, plan TEXT NOT NULL, result TEXT,
                    reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS file_ops_track ON file_ops(track_id,created_at);
                CREATE TABLE IF NOT EXISTS playlist_outputs (
                    path_key TEXT PRIMARY KEY, path TEXT NOT NULL, hash TEXT NOT NULL,
                    definition TEXT NOT NULL, dirty INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
            """)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connection(self, write=False):
        lock = self._write_lock if write else threading.RLock()
        with lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                if write:
                    db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def bind_root(self, root: Path) -> None:
        with self.connection(write=True) as db:
            saved = db.execute("SELECT value FROM metadata WHERE key='music_root'").fetchone()
            if saved and saved[0] != path_key(root):
                raise ValueError("이 DB에는 다른 음악 루트가 등록되어 있습니다. 새 DB로 시작하세요.")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('music_root',?)", (path_key(root),))

    def statistics(self) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT review_state,count(*) AS n FROM tracks WHERE file_state!='replaced' GROUP BY review_state").fetchall()
            values = {row["review_state"]: row["n"] for row in rows}
            unavailable = db.execute("SELECT count(*) FROM tracks WHERE file_state NOT IN ('ready','replaced')").fetchone()[0]
        return dict(total=sum(values.values()), confirmed=values.get("confirmed", 0),
                    unclassified=values.get("unclassified", 0), unresolved=values.get("unresolved", 0), unavailable=unavailable)

    def start_job(self, kind="scan") -> str:
        job_id = uuid4().hex
        with self.connection(write=True) as db:
            db.execute("INSERT INTO jobs(id,kind,state,started_at) VALUES (?,?,?,?)",
                       (job_id, kind, "running", now()))
        return job_id

    def recover_interrupted_jobs(self):
        with self.connection(write=True) as db:
            db.execute("UPDATE jobs SET state='interrupted',detail=? WHERE state IN ('running','paused')",
                       ("이전 실행이 중단되었습니다. 작업 이력을 확인하고 명시적으로 다시 시작하세요. 완료 기록은 보존했습니다.",))

    def mark_root_unavailable(self, root: Path):
        key = path_key(root)
        with self.connection(write=True) as db:
            for row in db.execute("SELECT id,path_key FROM tracks WHERE file_state!='replaced'").fetchall():
                if row["path_key"].startswith(key + "\\") or row["path_key"].startswith(key + "/"):
                    db.execute("UPDATE tracks SET file_state='unavailable' WHERE id=?", (row["id"],))

    def job_state(self, job_id: str, state: str, detail="") -> None:
        with self.connection(write=True) as db:
            db.execute("UPDATE jobs SET state=?,detail=?,ended_at=? WHERE id=?",
                       (state, detail, now() if state in {"completed", "partial", "cancelled", "failed"} else None, job_id))

    def observe(self, job_id: str, path: Path, payload: dict | None, error: str | None = None) -> None:
        with self.connection(write=True) as db:
            db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)",
                       (job_id, path_key(path), encode(payload) if payload else None, error))
            db.execute("UPDATE jobs SET processed=processed+?,failed=failed+? WHERE id=?",
                       (int(payload is not None), int(error is not None), job_id))

    @staticmethod
    def _insert(db, item, state="ready") -> str:
        track_id = uuid4().hex
        stamp = now()
        db.execute("""INSERT INTO tracks
            (id,path,path_key,hash,size,mtime_ns,title,artist,album,title_key,artist_key,version,
             duration,bitrate,sample_rate,grade,metadata_json,file_state,classification,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (track_id, item["path"], path_key(item["path"]), item["hash"], item["size"], item["mtime_ns"],
                    item["title"], item["artist"], item["album"], normalized(item["title"]), normalized(item["artist"]),
                    item["version"], item["duration"], item["bitrate"], item["sample_rate"], item["grade"],
                    encode(item), state, encode(empty_classification()), stamp, stamp))
        return track_id

    def finish_scan(self, job_id: str, root: Path, recursive: bool, failed_directories: list[str]) -> dict:
        counts = dict(new=0, unchanged=0, moved=0, changed=0, pending=0, missing=0)
        root_key = path_key(root)

        def in_scope(key: str) -> bool:
            path = Path(key)
            if not recursive and path_key(path.parent) != root_key:
                return False
            if key != root_key and not key.startswith(root_key + "\\") and not key.startswith(root_key + "/"):
                return False
            return not any(key == bad or key.startswith(bad + "\\") or key.startswith(bad + "/") for bad in failed_directories)

        with self.connection(write=True) as db:
            # Reconcile the complete inventory, not traversal order; copying must never steal the source ID.
            rows = db.execute("SELECT id,path_key,hash,file_state FROM tracks").fetchall()
            by_path = {row["path_key"]: row for row in rows}
            observed_keys = {row[0] for row in db.execute("SELECT path_key FROM observations WHERE job_id=?", (job_id,))}
            absent_by_hash = {}
            for row in rows:
                if row["file_state"] != "replaced" and in_scope(row["path_key"]) and row["path_key"] not in observed_keys:
                    absent_by_hash.setdefault(row["hash"], []).append(row)
            new_counts = {row["hash"]: row["n"] for row in db.execute("""
                SELECT json_extract(o.payload,'$.hash') AS hash, count(*) AS n
                FROM observations o LEFT JOIN tracks t ON o.path_key=t.path_key
                WHERE o.job_id=? AND o.payload IS NOT NULL AND t.id IS NULL GROUP BY hash""", (job_id,))}
            surviving_hashes = {row[0] for row in db.execute("""SELECT DISTINCT t.hash FROM tracks t
                JOIN observations o ON t.path_key=o.path_key
                WHERE o.job_id=? AND json_extract(o.payload,'$.hash')=t.hash""", (job_id,))}
            uncertain_hashes = {row[0] for row in db.execute("""SELECT t.hash FROM tracks t JOIN observations o
                ON t.path_key=o.path_key WHERE o.job_id=? AND o.error IS NOT NULL""", (job_id,))}
            uncertain_hashes.update(row["hash"] for row in rows if row["file_state"] != "replaced"
                                    and not in_scope(row["path_key"]) and row["path_key"] not in observed_keys)
            moved_ids = set()
            for observation in db.execute("SELECT * FROM observations WHERE job_id=?", (job_id,)).fetchall():
                old = by_path.get(observation["path_key"])
                if observation["error"]:
                    if old:
                        db.execute("UPDATE tracks SET file_state='unavailable' WHERE id=?", (old["id"],))
                    continue
                item = json.loads(observation["payload"])
                if old:
                    if old["hash"] == item["hash"]:
                        state = "link_pending" if old["file_state"] == "link_pending" else "ready"
                        db.execute("UPDATE tracks SET file_state=?,size=?,mtime_ns=?,observed_json=NULL WHERE id=?",
                                   (state, item["size"], item["mtime_ns"], old["id"]))
                        counts["unchanged"] += 1
                    else:
                        db.execute("UPDATE tracks SET file_state='external_change',observed_json=? WHERE id=?",
                                   (encode(item), old["id"]))
                        counts["changed"] += 1
                    continue
                candidates = absent_by_hash.get(item["hash"], [])
                # A surviving equal-hash source makes this a copy, even if another equal-hash record is missing.
                surviving = item["hash"] in surviving_hashes
                uncertain = item["hash"] in uncertain_hashes
                if not surviving and not uncertain and len(candidates) == 1 and new_counts[item["hash"]] == 1:
                    candidate = candidates[0]
                    db.execute("UPDATE tracks SET path=?,path_key=?,size=?,mtime_ns=?,file_state='ready',updated_at=? WHERE id=?",
                               (item["path"], path_key(item["path"]), item["size"], item["mtime_ns"], now(), candidate["id"]))
                    moved_ids.add(candidate["id"])
                    counts["moved"] += 1
                else:
                    pending = bool(candidates or uncertain) and not surviving
                    self._insert(db, item, "link_pending" if pending else "ready")
                    counts["pending" if pending else "new"] += 1
            for row in rows:
                if row["file_state"] != "replaced" and in_scope(row["path_key"]) and row["path_key"] not in observed_keys and row["id"] not in moved_ids:
                    db.execute("UPDATE tracks SET file_state='missing' WHERE id=?", (row["id"],))
                    counts["missing"] += 1
            for row in rows:
                if row["file_state"] != "replaced" and any(row["path_key"].startswith(bad + "\\") or row["path_key"].startswith(bad + "/") or row["path_key"] == bad for bad in failed_directories):
                    db.execute("UPDATE tracks SET file_state='unavailable' WHERE id=?", (row["id"],))
            failed = db.execute("SELECT failed FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            db.execute("UPDATE jobs SET state=?,detail=?,ended_at=? WHERE id=?",
                       ("partial" if failed else "completed", encode(counts), now(), job_id))
            db.execute("DELETE FROM observations WHERE job_id=?", (job_id,))
        return counts

    @staticmethod
    def _filter_clause(search="", major="", state="", review_only=False, mood="", concept=""):
        where, params = ["file_state!='replaced'"], []
        if search:
            escaped = normalized(search).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title_key LIKE ? ESCAPE '\\' OR artist_key LIKE ? ESCAPE '\\')")
            params += [f"%{escaped}%"] * 2
        if major:
            where.append("json_extract(classification,'$.major.value')=?")
            params.append(major)
        if state:
            where.append("review_state=?" if state in {"confirmed", "unresolved", "unclassified"} else "file_state=?")
            params.append(state)
        if review_only:
            where.append("(review_state='unresolved' OR file_state!='ready')")
        for axis, value in (("mood", mood), ("concept", concept)):
            if value:
                where.append(f"EXISTS (SELECT 1 FROM json_each(json_extract(classification,'$.{axis}.value')) WHERE value=?)")
                params.append(value)
        return " WHERE " + " AND ".join(where), params

    def list_tracks(self, search="", major="", state="", review_only=False, limit=200, offset=0, sort="title_key", descending=False,
                    mood="", concept=""):
        limit = max(1, min(500, int(limit)))
        sort = {"title_key": "title_key", "artist_key": "artist_key", "duration": "duration",
                "review_state": "review_state", "file_state": "file_state",
                "major": "json_extract(classification,'$.major.value')",
                "subgenre": "json_extract(classification,'$.subgenre.value')"}.get(sort, "title_key")
        clause, params = self._filter_clause(search, major, state, review_only, mood, concept)
        with self.connection() as db:
            total = db.execute("SELECT count(*) FROM tracks" + clause, params).fetchone()[0]
            rows = db.execute(f"SELECT * FROM tracks{clause} ORDER BY {sort} {'DESC' if descending else 'ASC'},id LIMIT ? OFFSET ?",
                              params + [limit, max(0, offset)]).fetchall()
        return [self._decode(row) for row in rows], total

    @staticmethod
    def _decode(row) -> dict:
        item = dict(row)
        item["classification"] = json.loads(item["classification"])
        item["metadata_json"] = json.loads(item["metadata_json"])
        item["observed_json"] = json.loads(item["observed_json"]) if item["observed_json"] else None
        return item

    def track(self, track_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if row is None:
            raise ValueError("곡을 찾을 수 없습니다.")
        return self._decode(row)

    def save_manual(self, track_id: str, changes: dict, expected_revision: int) -> None:
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise ValueError("곡이 변경되었습니다. 다시 열어 확인하세요.")
            if row["file_state"] != "ready":
                raise ValueError("파일 상태 검토를 먼저 해결하세요.")
            original = json.loads(row["classification"])
            updated = json.loads(row["classification"])
            for axis, value in changes.items():
                if axis not in AXES:
                    raise ValueError("알 수 없는 분류 항목입니다.")
                updated[axis] = dict(value=value, status="unresolved" if value is None else "confirmed",
                                     protected=True, source="manual", confidence=None, reason="사용자 검토")
            validate(updated)
            self._save_classification(db, row, original, updated, "manual")

    def unlock(self, track_id: str, axis: str) -> None:
        if axis not in AXES:
            raise ValueError("알 수 없는 항목입니다.")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            if row is None:
                raise ValueError("곡을 찾을 수 없습니다.")
            original = json.loads(row["classification"])
            updated = json.loads(row["classification"])
            updated[axis]["protected"] = False
            self._save_classification(db, row, original, updated, "unlock")

    @staticmethod
    def _save_classification(db, row, original, updated, action):
        db.execute("INSERT INTO classification_history(track_id,previous,current,action,created_at) VALUES (?,?,?,?,?)",
                   (row["id"], encode(original), encode(updated), action, now()))
        db.execute("UPDATE tracks SET classification=?,review_state=?,revision=revision+1,updated_at=? WHERE id=?",
                   (encode(updated), review_state(updated), now(), row["id"]))

    def accept_external_change(self, track_id: str, inherit_manual: bool, as_new=False) -> str:
        from .scanner import read_snapshot
        preview = self.track(track_id)
        current_item = read_snapshot(Path(preview["path"]))
        if not preview["observed_json"] or current_item["hash"] != preview["observed_json"]["hash"]:
            raise ValueError("파일이 다시 변경되었습니다. 재스캔하세요.")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            if row["file_state"] != "external_change" or not row["observed_json"]:
                raise ValueError("재스캔으로 변경을 확인하세요.")
            item = json.loads(row["observed_json"])
            if row["revision"] != preview["revision"] or item["hash"] != current_item["hash"]:
                raise ValueError("검토 내용이 변경되었습니다. 다시 열어 주세요.")
            if as_new:
                # Keep the old record and free the active path; it must not be matched by this path again.
                old_path = row["path_key"] + "#replaced-" + track_id
                db.execute("UPDATE tracks SET path_key=?,file_state='replaced',observed_json=NULL WHERE id=?", (old_path, track_id))
                return self._insert(db, item)
            original = json.loads(row["classification"])
            updated = empty_classification()
            if inherit_manual:
                for axis in AXES:
                    if original[axis]["protected"]:
                        updated[axis] = original[axis]
                # A protected subgenre requires the original parent even if the parent was automatic.
                if updated["subgenre"]["status"] == "confirmed":
                    updated["major"] = original["major"]
            validate(updated)
            self._save_classification(db, row, original, updated, "accept_external_change")
            self._update_metadata(db, track_id, item)
            return track_id

    @staticmethod
    def _update_metadata(db, track_id, item):
        db.execute("""UPDATE tracks SET hash=?,size=?,mtime_ns=?,title=?,artist=?,album=?,title_key=?,artist_key=?,version=?,
            duration=?,bitrate=?,sample_rate=?,grade=?,metadata_json=?,observed_json=NULL,file_state='ready' WHERE id=?""",
                   (item["hash"], item["size"], item["mtime_ns"], item["title"], item["artist"], item["album"],
                    normalized(item["title"]), normalized(item["artist"]), item["version"], item["duration"], item["bitrate"],
                    item["sample_rate"], item["grade"], encode(item), track_id))

    def link_candidates(self, track_id: str, offset=0, limit=200):
        track = self.track(track_id)
        with self.connection() as db:
            clause = " WHERE hash=? AND id!=? AND file_state IN ('missing','unavailable')"
            params = (track["hash"], track_id)
            total = db.execute("SELECT count(*) FROM tracks" + clause, params).fetchone()[0]
            rows = db.execute("SELECT * FROM tracks" + clause + " ORDER BY path_key,id LIMIT ? OFFSET ?",
                              (*params, min(500, max(1, limit)), max(0, offset))).fetchall()
        return [self._decode(row) for row in rows], total

    @staticmethod
    def _check_absent(path: str):
        try:
            Path(path).lstat()
        except FileNotFoundError:
            # A disconnected drive cannot prove that an old file is absent.
            if not Path(Path(path).anchor).is_dir():
                raise ValueError("기존 드라이브에 접근할 수 없어 부재를 확인하지 못했습니다.")
            return
        except OSError:
            raise ValueError("기존 경로의 부재를 확인하지 못했습니다. 접근 상태를 확인하세요.") from None
        raise ValueError("기존 경로에 파일이 존재합니다. 재스캔한 뒤 별도 파일로 검토하세요.")

    def resolve_link(self, track_id: str, expected_revision: int, old_id: str | None = None,
                     old_revision: int | None = None, inherit: bool | None = None, cancel=None) -> str:
        from .scanner import read_snapshot
        preview = self.track(track_id)
        item = read_snapshot(Path(preview["path"]), cancel)
        if item["hash"] != preview["hash"]:
            raise ValueError("새 파일이 변경되었습니다. 재스캔하세요.")
        old_preview = self.track(old_id) if old_id else None
        if old_preview:
            if inherit is None:
                raise ValueError("기존 분류의 유지 또는 초기화를 선택하세요.")
            self._check_absent(old_preview["path"])
        with self.connection(write=True) as db:
            if cancel and cancel.is_set():
                raise InterruptedError("연결 확인 취소")
            new = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            if (new["file_state"] != "link_pending" or new["revision"] != expected_revision
                    or new["hash"] != item["hash"] or new["path"] != item["path"]):
                raise ValueError("새 파일의 검토 상태가 변경되었습니다. 다시 열어 주세요.")
            if old_id:
                old = db.execute("SELECT * FROM tracks WHERE id=?", (old_id,)).fetchone()
                if (not old or old["file_state"] not in {"missing", "unavailable"} or old["hash"] != item["hash"]
                        or old["revision"] != old_revision or old["path"] != old_preview["path"]):
                    raise ValueError("기존 후보가 변경되었습니다. 다시 열어 주세요.")
                self._check_absent(old["path"])
                original = json.loads(old["classification"])
                updated = original if inherit else empty_classification()
                db.execute("UPDATE tracks SET path_key=?,file_state='replaced',revision=revision+1 WHERE id=?",
                           (new["path_key"] + "#replaced-" + track_id, track_id))
                self._save_classification(db, old, original, updated, "resolve_link_keep" if inherit else "resolve_link_reset")
                db.execute("UPDATE tracks SET path=?,path_key=?,size=?,mtime_ns=?,file_state='ready',observed_json=NULL WHERE id=?",
                           (item["path"], path_key(item["path"]), item["size"], item["mtime_ns"], old_id))
                result_id = old_id
            else:
                db.execute("UPDATE tracks SET file_state='ready',revision=revision+1,updated_at=? WHERE id=?", (now(), track_id))
                result_id = track_id
            self._update_metadata(db, result_id, item)
            db.execute("INSERT INTO file_links(old_id,new_id,decision,inherit,previous,current,created_at) VALUES (?,?,?,?,?,?,?)",
                       (old_id, track_id, "link" if old_id else "new", int(bool(inherit)),
                        encode(old_preview or {}), encode(preview), now()))
        return result_id

    @staticmethod
    def _bulk_snapshot(row):
        return encode({key: row[key] for key in ("hash", "path_key", "file_state", "classification")})

    def preview_bulk(self, operations: dict, scope="selected", filters=None, track_ids=None, cancel=None, progress=None):
        if scope not in {"selected", "filtered", "all"}:
            raise ValueError("대상 범위를 확인하세요.")
        if not operations:
            raise ValueError("수정할 항목을 선택하세요.")
        ids = list(dict.fromkeys(track_ids or []))
        if scope == "selected" and (not ids or len(ids) > 500):
            raise ValueError("현재 페이지에서 수정할 곡을 선택하세요.")
        job_id = uuid4().hex
        counts = dict(total=0, eligible=0, blocked=0, unchanged=0)
        with self.connection(write=True) as db:
            clause, params = self._filter_clause(**(filters or {})) if scope == "filtered" else self._filter_clause()
            if scope == "selected":
                clause += " AND id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
            counts["total"] = db.execute("SELECT count(*) FROM tracks" + clause, params).fetchone()[0]
            db.execute("INSERT INTO jobs(id,kind,state,detail,started_at) VALUES (?,?,?,?,?)",
                       (job_id, "manual_bulk", "preparing", encode(dict(scope=scope, filters=filters or {}, operations=operations)), now()))
            cursor = db.execute("SELECT * FROM tracks" + clause + " ORDER BY id", params)
            processed = 0
            while rows := cursor.fetchmany(200):
                if cancel and cancel.is_set():
                    db.execute("UPDATE jobs SET state='cancelled',ended_at=? WHERE id=?", (now(), job_id))
                    return dict(job_id=job_id, cancelled=True, **counts)
                for row in rows:
                    state, reason, proposed = "eligible", "", None
                    try:
                        if row["file_state"] != "ready":
                            raise ValueError("파일 상태 검토 필요")
                        original = json.loads(row["classification"])
                        updated = manual_patch(original, operations)
                        if updated == original:
                            state = "unchanged"
                        proposed = encode(updated)
                    except ValueError as error:
                        state, reason = "blocked", str(error)
                    counts[state] += 1
                    db.execute("INSERT INTO bulk_targets(job_id,track_id,revision,snapshot,proposed,state,reason) VALUES (?,?,?,?,?,?,?)",
                               (job_id, row["id"], row["revision"], self._bulk_snapshot(row), proposed, state, reason))
                processed += len(rows)
                if progress:
                    progress(processed, counts["total"])
            detail = dict(scope=scope, filters=filters or {}, operations=operations, counts=counts)
            db.execute("UPDATE jobs SET state='prepared',processed=?,failed=?,detail=? WHERE id=?",
                       (processed, counts["blocked"], encode(detail), job_id))
        return dict(job_id=job_id, **counts)

    def bulk_rows(self, job_id, offset=0, limit=200):
        with self.connection() as db:
            rows = db.execute("""SELECT b.track_id,b.state,b.reason,b.snapshot,b.proposed,t.title,t.artist,t.path
                FROM bulk_targets b JOIN tracks t ON b.track_id=t.id
                WHERE b.job_id=? ORDER BY b.sequence LIMIT ? OFFSET ?""", (job_id, min(500, max(1, limit)), max(0, offset))).fetchall()
        return [dict(row) for row in rows]

    def apply_bulk(self, job_id: str, cancel=None, progress=None):
        counts = dict(applied=0, stale=0, cancelled=0)
        with self.connection(write=True) as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["state"] != "prepared":
                raise ValueError("새 미리보기를 만든 뒤 저장하세요.")
            detail = json.loads(job["detail"])
            total = detail["counts"]["eligible"]
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
        try:
            while True:
                with self.connection(write=True) as db:
                    if cancel and cancel.is_set():
                        counts["cancelled"] = db.execute("SELECT count(*) FROM bulk_targets WHERE job_id=? AND state='eligible'", (job_id,)).fetchone()[0]
                        db.execute("UPDATE bulk_targets SET state='cancelled',reason='사용자 취소' WHERE job_id=? AND state='eligible'", (job_id,))
                        break
                    targets = db.execute("SELECT * FROM bulk_targets WHERE job_id=? AND state='eligible' ORDER BY sequence LIMIT 200", (job_id,)).fetchall()
                    if not targets:
                        break
                    chunk = dict(applied=0, stale=0)
                    for target in targets:
                        row = db.execute("SELECT * FROM tracks WHERE id=?", (target["track_id"],)).fetchone()
                        if not row or row["revision"] != target["revision"] or self._bulk_snapshot(row) != target["snapshot"]:
                            state, reason = "stale", "미리보기 이후 변경됨 · 새 미리보기 필요"
                        else:
                            updated = json.loads(target["proposed"])
                            validate(updated)
                            self._save_classification(db, row, json.loads(row["classification"]), updated, "bulk:" + job_id)
                            state, reason = "applied", ""
                        chunk[state] += 1
                        db.execute("UPDATE bulk_targets SET state=?,reason=? WHERE sequence=?", (state, reason, target["sequence"]))
                    db.execute("UPDATE jobs SET processed=?,failed=? WHERE id=?",
                               (counts["applied"] + chunk["applied"], detail["counts"]["blocked"] + counts["stale"] + chunk["stale"], job_id))
                for key in chunk:
                    counts[key] += chunk[key]
                if progress:
                    progress(counts["applied"] + counts["stale"], total)
            detail["result"] = counts
            state = "cancelled" if counts["cancelled"] else "partial" if counts["stale"] or detail["counts"]["blocked"] else "completed"
            self.job_state(job_id, state, encode(detail))
        except Exception:
            self.job_state(job_id, "failed", "일괄 저장 중 오류. 완료된 판정은 유지합니다. 새 미리보기로 다시 검토하세요.")
            raise
        return counts

    def duplicate_groups(self, tolerance=3.0):
        groups = []
        with self.connection() as db:
            keys = db.execute("""SELECT artist_key,title_key,version FROM tracks WHERE file_state='ready'
                AND artist_key!='' AND title_key!='' GROUP BY artist_key,title_key,version HAVING count(*)>1""").fetchall()
            for key in keys:
                rows = db.execute("""SELECT * FROM tracks WHERE file_state='ready' AND artist_key=? AND title_key=?
                    AND version=? ORDER BY duration,id""", tuple(key)).fetchall()
                cluster = []
                for row in rows:
                    if row["duration"] is None:
                        continue
                    if cluster and row["duration"] - cluster[0]["duration"] > tolerance:
                        self._append_group(db, groups, cluster)
                        cluster = []
                    cluster.append(row)
                self._append_group(db, groups, cluster)
        return groups

    @staticmethod
    def _append_group(db, groups, rows):
        if len(rows) < 2:
            return
        signature = ":".join(sorted(f"{row['id']}@{row['hash']}" for row in rows))
        if not db.execute("SELECT 1 FROM duplicate_decisions WHERE signature=?", (signature,)).fetchone():
            groups.append({"signature": signature, "tracks": [Library._decode(row) for row in rows]})

    def decide_duplicates(self, signature: str, decision: str, kept_ids: list[str]):
        if decision not in {"keep", "distinct"}:
            raise ValueError("판단 보류는 결론으로 저장하지 않습니다.")
        members = {part.split("@")[0] for part in signature.split(":")}
        if not kept_ids or not set(kept_ids) <= members:
            raise ValueError("유지할 파일을 선택하세요.")
        with self.connection(write=True) as db:
            for part in signature.split(":"):
                track_id, expected_hash = part.split("@")
                current = db.execute("SELECT hash,file_state FROM tracks WHERE id=?", (track_id,)).fetchone()
                if not current or current[0] != expected_hash or current[1] != "ready":
                    raise ValueError("후보가 변경되었습니다. 다시 확인하세요.")
            db.execute("INSERT OR REPLACE INTO duplicate_decisions VALUES (?,?,?,?)",
                       (signature, decision, encode(kept_ids), now()))

    def jobs(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT 100")]

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("같은 이름의 백업이 있습니다.")
        with self._write_lock, self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("백업 무결성 검증에 실패했습니다.")
            finally:
                target.close()
