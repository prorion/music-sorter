"""Conservative ID3 edits: preserve every byte outside TCON and restore raw tags."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_TAG_BYTES = 16 * 1024 * 1024


def synchsafe(data: bytes) -> int:
    if len(data) != 4 or any(n & 128 for n in data):
        raise ValueError("ID3 크기 정보가 올바르지 않습니다.")
    return sum(n << (7 * (3 - i)) for i, n in enumerate(data))


def pack_size(value: int) -> bytes:
    if not 0 <= value < 1 << 28:
        raise ValueError("ID3 크기 한도를 넘었습니다.")
    return bytes((value >> shift) & 127 for shift in (21, 14, 7, 0))


@dataclass
class TagLayout:
    raw: bytes
    version: int
    frames: list[tuple[bytes, bytes]]

    @property
    def offset(self):
        return len(self.raw)


def read_layout(path: Path) -> TagLayout:
    with path.open("rb") as stream:
        header = stream.read(10)
        if not header.startswith(b"ID3"):
            return TagLayout(b"", 4, [])
        if len(header) != 10 or header[3] not in {3, 4} or header[4:6] != b"\0\0":
            raise ValueError("이 ID3 버전·헤더 배치는 장르 기록을 지원하지 않습니다.")
        length = synchsafe(header[6:10])
        if length > MAX_TAG_BYTES:
            raise ValueError("ID3 영역이 16MiB를 넘어서 장르 기록을 보류합니다.")
        body = stream.read(length)
        if len(body) != length:
            raise ValueError("ID3 영역이 잘렸습니다.")
        # Appended ID3v2 may override the leading tag. Do not edit an ambiguous layout.
        stream.seek(max(0, path.stat().st_size - 138))
        trailer = stream.read()
        if trailer[-10:-7] == b"3DI" or trailer[-138:-135] == b"3DI":
            raise ValueError("파일 끝의 ID3v2 태그는 장르 기록을 지원하지 않습니다.")
    cursor, frames = 0, []
    while cursor < length:
        if body[cursor] == 0:
            if any(body[cursor:]):
                raise ValueError("ID3 패딩 배치가 올바르지 않습니다.")
            break
        frame = body[cursor:cursor + 10]
        if len(frame) != 10 or not re.fullmatch(rb"[A-Z0-9]{4}", frame[:4]):
            raise ValueError("ID3 프레임 배치를 확인할 수 없습니다.")
        size = synchsafe(frame[4:8]) if header[3] == 4 else int.from_bytes(frame[4:8], "big")
        end = cursor + 10 + size
        if not size or end > length:
            raise ValueError("ID3 프레임 크기가 올바르지 않습니다.")
        frames.append((frame[:4], body[cursor:end]))
        cursor = end
    return TagLayout(header + body, header[3], frames)


def genre_tag(layout: TagLayout, genre: str) -> bytes:
    payload = b"\x03" + genre.encode("utf-8") if layout.version == 4 else b"\x01" + genre.encode("utf-16")
    size = pack_size(len(payload)) if layout.version == 4 else len(payload).to_bytes(4, "big")
    frame = b"TCON" + size + b"\0\0" + payload
    kept = b"".join(raw for kind, raw in layout.frames if kind != b"TCON")
    length = max(len(layout.raw) - 10, len(kept) + len(frame) + 256)
    body = kept + frame + bytes(length - len(kept) - len(frame))
    return b"ID3" + bytes((layout.version, 0, 0)) + pack_size(length) + body


def digest(path: Path, offset=0) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(offset)
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def write_replacement(source: Path, target: Path, source_offset: int, tag: bytes):
    """Exclusive temporary creation; never truncate an existing user file."""
    with source.open("rb") as original, target.open("xb") as output:
        original.seek(source_offset)
        output.write(tag)
        while block := original.read(1024 * 1024):
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if digest(source, source_offset) != digest(target, len(tag)):
        raise ValueError("태그 밖 영역의 보존 검증에 실패했습니다. 원본은 변경하지 않았습니다.")
