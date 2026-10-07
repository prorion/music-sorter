"""Validated local DB restoration and data copies. Music and vault are never mutated."""
import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .database import Library, SCHEMA_VERSION, encode, now
from .settings import Settings
from .tag_io import digest


def verify_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('DB 무결성을 확인할 수 없습니다.')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
            raise ValueError('지원하지 않는 DB 객체가 있습니다.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in range(1, SCHEMA_VERSION + 1):
            raise ValueError('지원하지 않는 DB 버전입니다.')
        try:
            count = db.execute('SELECT count(*) FROM tracks').fetchone()[0]
            root = db.execute("SELECT value FROM metadata WHERE key='music_root'").fetchone()
            db.execute('SELECT id,path,hash,file_state,classification,revision FROM tracks LIMIT 1')
        except sqlite3.Error:
            raise ValueError('음악 DB 구조를 확인할 수 없습니다.') from None
    return dict(schema=version, tracks=count, music_root=root[0] if root else None)


def _copy_database(source, destination):
    if destination.exists():
        raise ValueError('준비 사본 경로에 파일이 있습니다.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(destination)
    try:
        original.backup(target)
    finally:
        original.close()
        target.close()
    return verify_database(destination)


def assert_idle(library):
    with library.connection() as db:
        if db.execute("SELECT 1 FROM file_ops WHERE state IN ('prepared','tag_done','file_done','undo_prepared') LIMIT 1").fetchone():
            raise ValueError('미완료 파일 작업을 먼저 복구하세요. 복원·이관을 보류합니다.')
        if db.execute("SELECT 1 FROM llm_requests WHERE state IN ('sending','remote','unknown','received') LIMIT 1").fetchone():
            raise ValueError('전송·원격·처리 확인 필요 분류 요청을 먼저 확인하세요. 비용 기록을 보존합니다.')


def prepare_restore(library, source):
    assert_idle(library)
    source = Path(source).resolve()
    if source == library.path.resolve():
        raise ValueError('현재 DB를 복원 대상으로 선택할 수 없습니다.')
    report = verify_database(source)
    current = verify_database(library.path)
    if report['music_root'] != current['music_root']:
        raise ValueError('현재 음악 루트와 다른 DB입니다. 별도 데이터 위치로 열어 주세요.')
    folder = library.path.parent / 'restore-plans' / uuid4().hex
    staged = folder / 'library.sqlite3'
    report = _copy_database(source, staged)
    return dict(staged=str(staged), hash=digest(staged), report=report, source=str(source), prepared_at=now())


def apply_restore(library, plan):
    staged = Path(plan['staged']).resolve()
    if not staged.is_relative_to((library.path.parent / 'restore-plans').resolve()) or digest(staged) != plan['hash']:
        raise ValueError('준비한 복원 사본이 변경되었습니다.')
    with library._write_lock:
        assert_idle(library)
        report = verify_database(staged)
        if report['music_root'] != verify_database(library.path)['music_root']:
            raise ValueError('음악 루트가 준비 이후 바뀌었습니다.')
        backup = library.path.parent / 'backups' / f'before-restore-{uuid4().hex}.sqlite3'
        library.backup(backup)
        with library.connection() as db:
            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise ValueError('DB 사용 중. 작업을 마치고 다시 복원하세요.')
        if any(Path(str(library.path) + suffix).exists() for suffix in ('-wal', '-shm')):
            raise ValueError('DB를 사용하는 연결이 남아 있습니다. 복원을 보류합니다.')
        temporary = library.path.with_name(f'.restore-{uuid4().hex}.sqlite3')
        _copy_database(staged, temporary)
        os.replace(temporary, library.path)
        try:
            fresh = Library(library.path)
            with fresh.connection(write=True) as db:
                db.execute("UPDATE tracks SET file_state='unavailable' WHERE file_state!='replaced'")
            fresh.recover_interrupted_jobs()
        except BaseException:
            # Keep the verified pre-restore backup and recover the active database only.
            recovery = library.path.with_name(f'.restore-recovery-{uuid4().hex}.sqlite3')
            _copy_database(backup, recovery)
            os.replace(recovery, library.path)
            raise
        return dict(backup=str(backup), tracks=report['tracks'], requires_rescan=True)


def export_settings(settings, destination):
    values = asdict(settings)
    for key in ('music_root', 'scan_exclude_folders', 'musicbrainz_contact', 'anthropic_workspace_id'):
        values.pop(key, None)
    path = Path(destination)
    # A user-selected export may replace a file after the native file dialog confirmation.
    temporary = path.with_name(f'.settings-export-{uuid4().hex}.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(values, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return values


def migrate_copy(library, settings, destination):
    destination = Path(destination).absolute()
    source = library.path.parent.resolve()
    if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()) or str(destination).startswith('\\\\'):
        raise ValueError('현재 데이터 위치와 겹치지 않는 로컬 폴더를 선택하세요.')
    for part in (destination, *destination.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('정션·심볼릭 링크 목적지는 지원하지 않습니다.')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('비어 있는 목적지 폴더를 선택하세요.')
    with library._write_lock:
        assert_idle(library)
        destination.mkdir(parents=True, exist_ok=True)
        copied_db = destination / 'music-sorter.sqlite3'
        library.backup(copied_db)
        checks = {}
        rollback = source / 'rollback'
        if rollback.exists():
            for path in rollback.rglob('*'):
                if path.is_symlink() or path.is_junction():
                    raise ValueError('복구 자료의 링크 배치를 확인하세요.')
                if not path.is_file():
                    continue
                target = destination / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                with path.open('rb') as original, target.open('xb') as copied:
                    shutil.copyfileobj(original, copied, 1024 * 1024)
                    copied.flush()
                    os.fsync(copied.fileno())
                checks[str(target.relative_to(destination))] = digest(path)
                if digest(target) != checks[str(target.relative_to(destination))]:
                    raise ValueError('이관 복구 자료 해시가 일치하지 않습니다.')
        copied = Library(copied_db)
        with copied.connection(write=True) as db:
            for row in db.execute('SELECT id,result FROM file_ops WHERE result IS NOT NULL').fetchall():
                result = json.loads(row['result'])
                if result.get('original_tag'):
                    original = Path(result['original_tag']).resolve()
                    if not original.is_relative_to(source):
                        raise ValueError('별도 복구 자료 경로는 현재 이관 절차에서 지원하지 않습니다.')
                    new = destination / original.relative_to(source)
                    if not new.is_file() or digest(new) != result['original_tag_hash']:
                        raise ValueError('파일 작업의 복구 자료 참조를 확인할 수 없습니다.')
                    result['original_tag'] = str(new)
                    db.execute('UPDATE file_ops SET result=? WHERE id=?', (encode(result), row['id']))
        settings.save(destination / 'settings.json')
        if (source / 'taxonomy.json').exists():
            from .catalog import load_catalog
            load_catalog(source / 'taxonomy.json')
            shutil.copy2(source / 'taxonomy.json', destination / 'taxonomy.json')
        report = verify_database(copied_db)
        manifest = dict(state='verified_copy', database_hash=digest(copied_db), rollback_hashes=checks, report=report)
        (destination / 'migration.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        return dict(destination=str(destination), report=report, rollback_files=len(checks))
