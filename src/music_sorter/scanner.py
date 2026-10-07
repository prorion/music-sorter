from __future__ import annotations

import hashlib
import os
import re
import threading
import unicodedata
from pathlib import Path

from mutagen.mp3 import MP3

from .database import Library, normalized, path_key

VERSION_PATTERN = re.compile(r"\b(live|remix|instrumental|acoustic|remaster(?:ed)?|karaoke)\b", re.I)


def parse_filename(path: Path) -> tuple[str, str]:
    stem = unicodedata.normalize("NFC", path.stem)
    match = re.match(r"^(.+?)\s*-\s*(?:\d{1,3}\s*-\s*)?(.+)$", stem)
    return (match[1].strip(), match[2].strip()) if match else ("", stem)


def read_snapshot(path: Path, cancel: threading.Event | None = None) -> dict:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise InterruptedError("스캔을 취소했습니다.")
            digest.update(block)
        stream.seek(0)
        audio = MP3(stream)
        tags = audio.tags

        def text(name: str) -> str:
            frames = tags.getall(name) if tags else []
            return "; ".join(str(t) for frame in frames for t in getattr(frame, "text", []))

        fallback_artist, fallback_title = parse_filename(path)
        tagged_title, tagged_artist, album = text("TIT2"), text("TPE1"), text("TALB")
        title, artist = tagged_title or fallback_title, tagged_artist or fallback_artist
        genre, year = text("TCON"), text("TDRC")
        has_lyrics = bool(tags and (tags.getall("USLT") or tags.getall("SYLT")))
        has_priv = bool(tags and tags.getall("PRIV"))
        grade = "A" if all((tagged_title, tagged_artist, album, genre, year, has_lyrics, has_priv)) else (
            "B" if tagged_title and tagged_artist and album else "C" if tagged_title or tagged_artist else "D")
        after = os.fstat(stream.fileno())
        current = path.stat()
        state = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino)
        if state(before) != state(after) or state(after) != state(current):
            raise OSError("읽는 동안 파일이 변경되었습니다.")
        return dict(path=str(path.resolve()), hash=digest.hexdigest(), size=after.st_size,
                    mtime_ns=after.st_mtime_ns, title=unicodedata.normalize("NFC", title),
                    artist=unicodedata.normalize("NFC", artist), album=album, genre=genre, year=year,
                    version="|".join(sorted(set(normalized(m.group(1)) for m in VERSION_PATTERN.finditer(title + " " + path.stem)))),
                    duration=audio.info.length, bitrate=audio.info.bitrate, sample_rate=audio.info.sample_rate,
                    grade=grade, has_lyrics=has_lyrics, has_priv=has_priv,
                    metadata_source="id3" if tagged_title and tagged_artist else "id3_filename")


class ScanControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self.paused = threading.Event()

    def checkpoint(self):
        while self.paused.is_set():
            if self.cancelled.wait(0.1):
                raise InterruptedError("스캔을 취소했습니다.")
        if self.cancelled.is_set():
            raise InterruptedError("스캔을 취소했습니다.")


def scan_library(library: Library, root: Path, recursive=True, control=None, progress=None) -> dict:
    root = root.resolve()
    if not root.is_dir():
        library.mark_root_unavailable(root)
        job_id = library.start_job()
        library.job_state(job_id, "failed", "음악 루트에 접근할 수 없습니다. 누락 판정은 하지 않았습니다.")
        raise ValueError("음악 폴더 접근 상태를 확인하세요.")
    library.bind_root(root)
    control = control or ScanControl()
    progress = progress or (lambda count, failed: None)
    job_id = library.start_job()
    failed_directories: list[str] = []
    count = failed = 0

    def walk_error(error):
        nonlocal failed
        bad = Path(error.filename or root)
        failed_directories.append(path_key(bad))
        library.observe(job_id, bad, None, "폴더 접근 실패")
        failed += 1

    try:
        for directory, subdirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            control.checkpoint()
            subdirs[:] = sorted(name for name in subdirs if not (Path(directory) / name).is_symlink()
                                and not (Path(directory) / name).is_junction()) if recursive else []
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix.casefold() != ".mp3" or path.is_symlink():
                    continue
                control.checkpoint()
                try:
                    path.resolve().relative_to(root)
                    snapshot = read_snapshot(path, control.cancelled)
                    library.observe(job_id, path, snapshot)
                    count += 1
                except InterruptedError:
                    raise
                except Exception as error:
                    # Persist a category, not a raw exception that might expose metadata or credentials.
                    library.observe(job_id, path, None, type(error).__name__)
                    failed += 1
                if (count + failed) % 10 == 0:
                    progress(count, failed)
        control.checkpoint()
        result = library.finish_scan(job_id, root, recursive, failed_directories)
        progress(count, failed)
        return dict(job_id=job_id, processed=count, failed=failed, **result)
    except InterruptedError:
        library.job_state(job_id, "cancelled", "완료 전 스캔 취소. 누락 판정은 적용하지 않았습니다.")
        return dict(job_id=job_id, processed=count, failed=failed, cancelled=True)
    except Exception:
        library.job_state(job_id, "failed", "스캔 실패. 재실행 후 폴더 상태를 확인하세요.")
        raise
