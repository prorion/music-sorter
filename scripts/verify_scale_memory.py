"""Bounded page memory on an existing synthetic DB, separately from query speed."""
import argparse
import ctypes
import gc
import json
import time
import tracemalloc
import sqlite3
from contextlib import closing
from pathlib import Path

from music_sorter.database import Library


def working_set():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                       'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel, psapi = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(working_set_bytes=counters.WorkingSetSize, process_peak_working_set_bytes=counters.PeakWorkingSetSize)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('새 측정 출력 파일을 사용하세요.')
    with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != 4 or db.execute("SELECT count(*) FROM tracks WHERE path NOT LIKE 'virtual/%'").fetchone()[0]:
            raise ValueError('스키마 4의 합성 virtual/ 벤치마크 DB만 사용하세요.')
    library = Library(args.db)
    with library.connection() as db:
        count = db.execute('SELECT count(*) FROM tracks').fetchone()[0]
    library.list_tracks()  # Warm-up imports/SQLite before baseline.
    gc.collect()
    before = working_set()
    tracemalloc.start()
    start = time.perf_counter()
    maximum = 0
    for index in range(100):
        tracks, _ = library.list_tracks(offset=(index * 200) % max(200, count), limit=200)
        maximum = max(maximum, len(tracks))
        del tracks
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after = working_set()
    report = dict(rows=count, queries=100, max_loaded_tracks=maximum, python_retained_bytes=current, python_peak_bytes=peak,
                  before=before, after=after, elapsed_seconds=round(time.perf_counter() - start, 3),
                  working_set_growth_bytes=after['working_set_bytes'] - before['working_set_bytes'],
                  scope='synthetic_db_backend_pages_only_not_full_scan_or_qt_widget_memory')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
