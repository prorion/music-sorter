import hashlib
from pathlib import Path

import pytest

from music_sorter.database import Library
from music_sorter.scanner import parse_filename


def fake_snapshot(path: Path, cancel=None):
    artist, title = parse_filename(path)
    stat = path.stat()
    return dict(path=str(path.resolve()), hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, title=title, artist=artist, album="",
                version="", duration=180.0, bitrate=320000, sample_rate=44100, grade="D", genre="",
                has_lyrics=False, has_priv=False, year="")


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "db" / "library.sqlite3")


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    return root


@pytest.fixture
def fake_reader(monkeypatch):
    monkeypatch.setattr("music_sorter.scanner.read_snapshot", fake_snapshot)


@pytest.fixture
def song(root):
    path = root / "가수 - 제목.mp3"
    path.write_bytes(b"original-audio")
    return path
