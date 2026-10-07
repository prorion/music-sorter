"""Synthetic DB query benchmark; this does not measure actual MP3 scan throughput."""
import argparse
import json
import statistics
import time
from pathlib import Path

from music_sorter.classification import empty_classification
from music_sorter.database import Library, encode, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=300000)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("새 벤치마크 출력 디렉터리를 지정하세요.")
    args.output.mkdir(parents=True)
    library = Library(args.output / "benchmark.sqlite3")
    stamp = now()
    classification = empty_classification()
    classification["major"] = dict(value="가요", status="confirmed", protected=False, source="benchmark", confidence=None, reason="")
    payload = encode(classification)
    def records():
        for index in range(args.count):
            yield (f"{index:032x}", f"virtual/{index}.mp3", f"virtual/{index}.mp3", f"{index:064x}",
                   1000, 0, f"곡 {index:06d}", f"가수 {index % 1000:04d}", "앨범", f"곡 {index:06d}",
                   f"가수 {index % 1000:04d}", "", 180, 320000, 44100, "C", "{}", payload, stamp, stamp)
    with library.connection(write=True) as db:
        db.executemany("""INSERT INTO tracks(id,path,path_key,hash,size,mtime_ns,title,artist,album,title_key,
            artist_key,version,duration,bitrate,sample_rate,grade,metadata_json,classification,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", records())
    report = {"rows": args.count, "query_samples": 20, "page_limit": 200, "measurements_ms": {}}
    for name, kwargs in (("page", {}), ("substring_search", {"search": "가수 0001"}),
                         ("major_filter", {"major": "가요"}), ("artist_sort", {"sort": "artist_key"})):
        times = []
        for _ in range(20):
            start = time.perf_counter()
            rows, total = library.list_tracks(**kwargs)
            times.append((time.perf_counter() - start) * 1000)
            assert len(rows) <= 200
        report["measurements_ms"][name] = {"median": round(statistics.median(times), 2),
                                          "p95": round(sorted(times)[18], 2), "matched": total}
    (args.output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
