"""Kill only owned child processes at durable journal checkpoints, using MP3 copies."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from music_sorter.database import Library
from music_sorter.file_ops import FileOperations
from music_sorter.scanner import scan_library
from music_sorter.tag_io import digest


def child(folder, stage, job_id):
    library = Library(folder / 'user-data' / 'music-sorter.sqlite3')
    engine = FileOperations(library, folder / 'music')
    def checkpoint(value):
        if value != stage:
            return
        with (folder / 'checkpoint.json').open('x', encoding='utf-8') as stream:
            json.dump({'stage': value, 'pid': os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        time.sleep(60)
    if stage.startswith('undo_'):
        engine.undo(engine.operations(job_id)[0]['id'], fault=checkpoint)
    else:
        engine.apply(job_id, fault=checkpoint)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--child', choices=['prepared', 'tag_done', 'file_done', 'recorded', 'undo_prepared', 'undo_tag_done', 'undo_file_done'])
    parser.add_argument('--job')
    args = parser.parse_args()
    if args.child:
        child(args.output, args.child, args.job)
        return
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError('원본 밖의 새 검증 폴더를 사용하세요.')
    original_hash = digest(source)
    output.mkdir(parents=True)
    reports = []
    for stage in ('prepared', 'tag_done', 'file_done', 'recorded', 'undo_prepared', 'undo_tag_done', 'undo_file_done'):
        folder = output / stage
        root = folder / 'music'
        root.mkdir(parents=True)
        copied = root / source.name
        shutil.copy2(source, copied)
        library = Library(folder / 'user-data' / 'music-sorter.sqlite3')
        scan_library(library, root)
        track = library.list_tracks()[0][0]
        library.save_manual(track['id'], dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=[]), track['revision'])
        engine = FileOperations(library, root)
        job = engine.preview([track['id']], organize=True, rename=True, write_genre=True)
        if stage.startswith('undo_'):
            assert engine.apply(job) == {'completed': 1, 'blocked': 0}
        environment = dict(os.environ)
        environment['PYTHONPATH'] = os.pathsep.join((str(Path(__file__).resolve().parents[1] / 'src'), str(Path(sys.prefix) / 'Lib' / 'site-packages')))
        # Windows venv redirectors create another process. Launch the base interpreter
        # directly with this venv's libraries so Popen owns exactly the killed worker.
        process = subprocess.Popen([sys._base_executable, str(Path(__file__).resolve()), '--output', str(folder), '--child', stage, '--job', job],
                                   stdin=subprocess.DEVNULL, env=environment)
        deadline = time.monotonic() + 30
        try:
            while not (folder / 'checkpoint.json').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            marker = json.loads((folder / 'checkpoint.json').read_text('utf-8'))
            assert marker == {'stage': stage, 'pid': process.pid}
            process.kill()
            killed = process.wait(timeout=10)
            assert killed != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        fresh = Library(library.path)
        fresh.recover_interrupted_jobs()
        resumed = FileOperations(fresh, root)
        recovery = resumed.recover()
        if recovery['pending']:
            assert resumed.resume(job) == {'completed': 1, 'blocked': 0}
        operation = resumed.operations(job)[0]
        if operation['state'] != 'undone':
            assert operation['state'] == 'recorded'
            resumed.undo(operation['id'])
        current = fresh.track(track['id'])
        assert current['path'] == str(copied.resolve()) and current['hash'] == original_hash and digest(copied) == original_hash
        assert current['classification']['major']['protected']
        reports.append(dict(stage=stage, child_killed=True, recovery=recovery, restored_exactly=True))
    assert digest(source) == original_hash
    report = dict(cases=reports, originals_unchanged=True, original_sha256=original_hash)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
