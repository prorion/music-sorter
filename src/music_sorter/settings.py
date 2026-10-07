from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "music-sorter"


def data_directory() -> Path:
    base = default_data_directory()
    locator = base / 'data-location.json'
    if not locator.exists():
        return base
    if locator.stat().st_size > 8192:
        raise ValueError('데이터 위치 설정 크기를 확인하세요.')
    value = json.loads(locator.read_text('utf-8'))
    target = Path(value['directory'])
    if not target.is_absolute() or not target.is_dir():
        raise ValueError('지정한 데이터 폴더에 접근할 수 없습니다. 폴더 연결 상태를 확인하세요.')
    return target


def activate_data_directory(destination):
    from .maintenance import verify_database
    from .catalog import load_catalog
    from .tag_io import digest
    from PySide6.QtCore import QLockFile
    destination = Path(destination).absolute()
    for part in (destination, *destination.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('링크를 사용하는 데이터 위치는 지원하지 않습니다.')
    lock = QLockFile(str(destination / 'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        raise ValueError('다른 앱이 이관 목적지를 사용하고 있습니다.')
    try:
        manifest = json.loads((destination / 'migration.json').read_text('utf-8'))
        if manifest['state'] != 'verified_copy' or digest(destination / 'music-sorter.sqlite3') != manifest['database_hash']:
            raise ValueError('검증한 이관 DB가 변경되었습니다. 새 사본을 준비하세요.')
        verify_database(destination / 'music-sorter.sqlite3')
        Settings.load(destination / 'settings.json')
        load_catalog(destination / 'taxonomy.json')
        for name, expected in manifest['rollback_hashes'].items():
            path = (destination / name).resolve()
            if not path.is_relative_to(destination) or digest(path) != expected:
                raise ValueError('이관 복구 자료가 변경되었습니다.')
        base = default_data_directory()
        base.mkdir(parents=True, exist_ok=True)
        target = base / 'data-location.json'
        temporary = base / 'data-location.json.tmp'
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump({'directory': str(destination)}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        lock.unlock()


@dataclass
class Settings:
    music_root: str = ""
    theme: str = "system"
    font_scale: float = 1.0
    include_subfolders: bool = True
    scan_exclude_folders: list[str] = field(default_factory=list)
    notify_on_completion: bool = True
    classify_provider: str = "anthropic"
    classify_model: str = "claude-haiku-4-5"
    escalate_provider: str = "anthropic"
    escalate_model: str = "claude-sonnet-5-5"
    anthropic_workspace_id: str = ""
    duplicate_tolerance_seconds: float = 3.0
    musicbrainz_enabled: bool = True
    musicbrainz_contact: str = ''
    lastfm_enabled: bool = True
    rollback_limit_gib: float = 10.0
    playlist_format: str = 'm3u8'
    llm_tracks_per_request: int = 20
    llm_max_output_tokens_per_track: int = 1000
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    include_lyrics_default: bool = False

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()
        values = json.loads(path.read_text("utf-8"))
        settings = cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        settings.validate()
        return settings

    def validate(self) -> None:
        for name in ('include_subfolders', 'notify_on_completion', 'musicbrainz_enabled', 'lastfm_enabled', 'include_lyrics_default'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('설정의 논리값 형식을 확인하세요.')
        for name, low, high in [('llm_tracks_per_request', 1, 20), ('llm_max_output_tokens_per_track', 256, 2000),
                                ('llm_timeout_seconds', 10, 180), ('llm_max_retries', 0, 3)]:
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError(f'{name} 설정 범위를 확인하세요 ({low}~{high}).')
        if not isinstance(self.scan_exclude_folders, list) or len(self.scan_exclude_folders) > 100 or any(not isinstance(p, str) or not p.strip() or Path(p).is_absolute() or '..' in Path(p).parts for p in self.scan_exclude_folders):
            raise ValueError('제외 폴더는 음악 루트 아래의 상대 경로 목록으로 입력하세요.')
        if self.theme not in {"system", "light", "dark"}:
            raise ValueError("테마 값이 올바르지 않습니다.")
        if not isinstance(self.playlist_format, str) or self.playlist_format not in {'m3u8', 'm3u'}:
            raise ValueError('재생목록 형식은 m3u8 또는 m3u입니다.')
        if type(self.font_scale) not in (int, float) or not 0.8 <= self.font_scale <= 2:
            raise ValueError("글자 크기 범위를 확인하세요.")
        if type(self.duplicate_tolerance_seconds) not in (int, float) or not 0 <= self.duplicate_tolerance_seconds <= 30:
            raise ValueError("중복 길이 허용값은 0~30초입니다.")
        if type(self.rollback_limit_gib) not in (int, float) or not .1 <= self.rollback_limit_gib <= 10000:
            raise ValueError('ID3 복구 보관 한도는 0.1~10,000GiB입니다.')
        if not isinstance(self.musicbrainz_contact, str) or len(self.musicbrainz_contact) > 250 or any(ord(c) < 33 or ord(c) > 126 for c in self.musicbrainz_contact):
            raise ValueError('MusicBrainz 연락처는 공백·줄바꿈 없는 이메일 또는 HTTPS URL입니다.')
        if self.musicbrainz_contact and not ('@' in self.musicbrainz_contact or self.musicbrainz_contact.startswith('https://')):
            raise ValueError('MusicBrainz 연락처에 이메일 또는 HTTPS URL을 입력하세요.')
        for provider in (self.classify_provider, self.escalate_provider):
            if provider not in {"anthropic", "openai"}:
                raise ValueError("API 서비스를 확인하세요.")
        if not self.classify_model.strip() or not self.escalate_model.strip():
            raise ValueError("모델 ID를 입력하세요.")
        if not isinstance(self.anthropic_workspace_id, str) or any(ord(char) < 33 or ord(char) > 126 for char in self.anthropic_workspace_id):
            raise ValueError("Claude 워크스페이스 ID의 공백·줄바꿈을 확인하세요.")

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)


class CredentialStore:
    """Explicitly select Windows storage; never accept an automatic plaintext backend."""

    profile_keys = None
    profile_defaults = {}

    @staticmethod
    def backend():
        if os.name != "nt":
            raise RuntimeError("현재 버전의 키 보관은 Windows에서만 지원합니다.")
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def get(self, provider: str) -> str | None:
        if self.profile_keys is not None:
            return self.profile_keys.get(provider)
        return self.backend().get_password("music-sorter", provider)

    def set(self, provider: str, value: str) -> None:
        if not value.strip():
            raise ValueError("빈 키는 등록할 수 없습니다.")
        self.backend().set_password("music-sorter", provider, value.strip())

    def delete(self, provider: str) -> None:
        backend = self.backend()
        if backend.get_password("music-sorter", provider) is not None:
            backend.delete_password("music-sorter", provider)
