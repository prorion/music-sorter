"""Explicit duplicate removal with a durable journal and Windows-only recycling."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from .database import encode, now
from .file_ops import inside_root
from .tag_io import digest


def windows_recycle(path: Path, expected_hash=None) -> str:
    if os.name != 'nt':
        raise ValueError('Windows 휴지통에서만 지원하는 작업입니다.')
    import pythoncom
    import pywintypes
    import win32file
    import win32con
    from win32com.shell import shell, shellcon
    from win32com.server.exception import COMException
    from win32com.server.policy import DesignatedWrapPolicy

    class RecycleSink(DesignatedWrapPolicy):
        _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
        _public_methods_ = ['StartOperations', 'FinishOperations', 'PreRenameItem', 'PostRenameItem',
                            'PreMoveItem', 'PostMoveItem', 'PreCopyItem', 'PostCopyItem',
                            'PreDeleteItem', 'PostDeleteItem', 'PreNewItem', 'PostNewItem',
                            'UpdateProgress', 'ResetTimer', 'PauseTimer', 'ResumeTimer']

        def __init__(self):
            self._wrap_(self)
            self.destination = None
            self.result = None

        def __getattr__(self, name):
            if name in self._public_methods_:
                return lambda *_: 0
            raise AttributeError(name)

        def PreDeleteItem(self, flags, item):
            if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                raise COMException(desc='영구 삭제는 허용하지 않습니다.', scode=-2147467260)
            return 0

        def PostDeleteItem(self, flags, item, result, created):
            self.result = result
            if created is not None:
                self.destination = created.GetDisplayName(shellcon.SIGDN_FILESYSPATH)
            return 0

    # Permit the recycle rename, while denying writers until the operation finishes.
    try:
        handle = win32file.CreateFile(str(path), win32con.GENERIC_READ,
                                      win32con.FILE_SHARE_READ | win32con.FILE_SHARE_DELETE,
                                      None, win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)
    except pywintypes.error as error:
        raise OSError('파일 잠금·권한 문제로 휴지통 이동을 보류합니다.') from error
    try:
        if expected_hash is not None and digest(path) != expected_hash:
            raise ValueError('휴지통 이동 직전 파일 내용이 변경되었습니다.')
    except BaseException:
        handle.Close()
        raise
    pythoncom.CoInitialize()
    operation = sink = wrapped = item = None
    try:
        operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                                              pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
        # Windows 8+: require recycling and abort on errors; never use a legacy delete fallback.
        operation.SetOperationFlags(0x20000000 | 0x00080000 | shellcon.FOFX_EARLYFAILURE
                                    | shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT
                                    | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NO_CONNECTED_ELEMENTS)
        sink = RecycleSink()
        wrapped = pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)
        item = shell.SHCreateItemFromParsingName(str(path.resolve()), None, shell.IID_IShellItem)
        operation.DeleteItem(item, wrapped)
        operation.PerformOperations()
        if operation.GetAnyOperationsAborted() or sink.result is None or sink.result & 0x80000000 or not sink.destination:
            raise OSError('휴지통 이동을 확인하지 못했습니다. 영구 삭제로 대체하지 않습니다.')
        return sink.destination
    except pywintypes.com_error as error:
        raise OSError('Windows 휴지통 이동 실패. 파일 잠금·권한·휴지통 지원을 확인하세요.') from error
    finally:
        item = wrapped = sink = operation = None
        pythoncom.CoUninitialize()
        handle.Close()


class DuplicateRemoval:
    def __init__(self, library, root: Path, tolerance=3.0, recycle=None):
        self.library, self.root, self.tolerance = library, root.absolute(), tolerance
        self.recycle = recycle

    def _busy(self, ids):
        placeholders = ','.join('?' for _ in ids)
        with self.library.connection() as db:
            if db.execute("SELECT 1 FROM jobs WHERE kind='scan' AND state IN ('running','paused') LIMIT 1").fetchone():
                raise ValueError('스캔을 마치거나 취소한 뒤 삭제하세요.')
            if db.execute(f"SELECT 1 FROM file_ops WHERE track_id IN ({placeholders}) AND state IN ('prepared','tag_done','file_done','undo_prepared') LIMIT 1", ids).fetchone():
                raise ValueError('후보의 미완료 파일 작업을 먼저 확인하세요.')
            if db.execute(f"SELECT 1 FROM llm_targets WHERE track_id IN ({placeholders}) AND state IN ('prepared','sending','unknown','remote','received') LIMIT 1", ids).fetchone():
                raise ValueError('후보의 미완료 분류 작업을 먼저 확인하세요.')

    def _check(self, expected, check_hash=True):
        current = self.library.track(expected['id'])
        if any(current[key] != expected[key] for key in ('path', 'hash', 'revision', 'file_state')) or current['file_state'] != 'ready':
            raise ValueError('후보의 DB 상태가 변경되었습니다. 다시 비교하세요.')
        path = inside_root(self.root, Path(current['path']))
        if str(path).startswith('\\\\') or not path.is_file() or path.suffix.lower() != '.mp3':
            raise ValueError('접근 가능한 로컬 MP3 파일만 삭제할 수 있습니다.')
        before = path.stat()
        if check_hash and digest(path) != current['hash']:
            raise ValueError('후보의 파일 내용이 변경되었습니다. 재스캔하세요.')
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('파일 확인 중 내용이 변경되었습니다. 다시 비교하세요.')
        return path

    def preview(self, group, selected):
        with self.library._write_lock:
            self.library.bind_root(self.root)
            ids = list(dict.fromkeys(selected))
            members = {track['id']: track for track in group['tracks']}
            if not ids or not set(ids) < set(members):
                raise ValueError('삭제할 파일을 선택하고 후보를 한 개 이상 남겨 주세요.')
            current = next((item for item in self.library.duplicate_groups(self.tolerance)
                            if item['signature'] == group['signature']), None)
            if current is None:
                raise ValueError('후보 그룹이 변경되었습니다. 다시 비교하세요.')
            self._busy(list(members))
            snapshots = [{key: track[key] for key in ('id', 'path', 'hash', 'revision', 'file_state')}
                         for track in group['tracks']]
            for snapshot in snapshots:
                self._check(snapshot)
            return dict(signature=group['signature'], selected=ids, candidates=snapshots)

    def _persist(self, journal, state='running'):
        processed = sum(item['state'] == 'recycled' for item in journal['targets'])
        failed = sum(item['state'] in {'failed', 'unknown', 'not_attempted'} for item in journal['targets'])
        lines = [f"휴지통 이동 {processed} · 보류/확인 필요 {failed}",
                 '복원: Windows 휴지통에서 복원 후 폴더 스캔. 자동 재삭제 없음.']
        lines += [f"{item['state']} · {item['path']}" + (f" · {item['reason']}" if item.get('reason') else '')
                  for item in journal['targets']]
        with self.library.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('duplicate_delete:' + journal['job_id'], encode(journal)))
            db.execute('UPDATE jobs SET state=?,processed=?,failed=?,detail=?,ended_at=? WHERE id=?',
                       (state, processed, failed, '\n'.join(lines), now() if state != 'running' else None, journal['job_id']))

    def apply(self, plan, progress=None):
        progress = progress or (lambda *_: None)
        with self.library._write_lock:
            group = dict(signature=plan['signature'], tracks=plan['candidates'])
            self.preview(group, plan['selected'])
            self.library.backup(self.library.path.parent / 'backups' / f'before-recycle-{uuid4().hex}.sqlite3')
            job_id = self.library.start_job('duplicate_delete')
            targets = [dict(item, state='planned') for item in plan['candidates'] if item['id'] in plan['selected']]
            journal = dict(job_id=job_id, targets=targets)
            self._persist(journal)
            for target in targets:
                try:
                    # Recheck survivors and all remaining targets immediately before every move.
                    self._busy([item['id'] for item in plan['candidates']])
                    removed = {item['id'] for item in targets if item['state'] == 'recycled'}
                    for candidate in plan['candidates']:
                        if candidate['id'] not in removed:
                            self._check(candidate)
                    target['state'] = 'sending'
                    self._persist(journal)
                    destination = (self.recycle(Path(target['path'])) if self.recycle is not None
                                   else windows_recycle(Path(target['path']), expected_hash=target['hash']))
                    if Path(target['path']).exists():
                        raise OSError('원래 파일이 남아 있어 휴지통 이동을 확정하지 못했습니다.')
                    target.update(state='recycled', recycle_path=str(destination))
                    with self.library.connection(write=True) as db:
                        db.execute("UPDATE tracks SET file_state='missing',revision=revision+1,updated_at=? WHERE id=?", (now(), target['id']))
                        # Track state and durable success receipt commit together.
                        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('duplicate_delete:' + job_id, encode(journal)))
                except (ValueError, OSError) as error:
                    was_sending = target['state'] == 'sending'
                    target.update(state='unknown' if was_sending else 'failed', reason=str(error))
                self._persist(journal)
                progress(sum(item['state'] == 'recycled' for item in targets), sum(item['state'] in {'failed', 'unknown'} for item in targets))
            count = sum(item['state'] == 'recycled' for item in targets)
            state = 'completed' if count == len(targets) else 'partial' if count else 'failed'
            self._persist(journal, state)
            return dict(job_id=job_id, processed=count, failed=len(targets)-count, targets=targets)

    def recover(self):
        with self.library._write_lock, self.library.connection() as db:
            records = db.execute("SELECT value FROM metadata WHERE key LIKE 'duplicate_delete:%'").fetchall()
        for record in records:
            journal = json.loads(record[0])
            pending = [item for item in journal['targets'] if item['state'] in {'planned', 'sending'}]
            if not pending:
                continue
            for item in pending:
                item.update(state='unknown' if item['state'] == 'sending' else 'not_attempted',
                            reason='이전 실행 중단. 휴지통과 파일 상태를 확인하고 재스캔하세요. 자동 재삭제 없음.')
            count = sum(item['state'] == 'recycled' for item in journal['targets'])
            self._persist(journal, 'partial' if count else 'interrupted')
