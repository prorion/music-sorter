from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "music-sorter"


@dataclass
class Settings:
    music_root: str = ""
    theme: str = "system"
    font_scale: float = 1.0
    include_subfolders: bool = True
    notify_on_completion: bool = True
    classify_provider: str = "anthropic"
    classify_model: str = "claude-haiku-4-5"
    escalate_provider: str = "anthropic"
    escalate_model: str = "claude-sonnet-5-5"
    duplicate_tolerance_seconds: float = 3.0

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()
        values = json.loads(path.read_text("utf-8"))
        settings = cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.theme not in {"system", "light", "dark"}:
            raise ValueError("테마 값이 올바르지 않습니다.")
        if not isinstance(self.font_scale, (int, float)) or not 0.8 <= self.font_scale <= 2:
            raise ValueError("글자 크기 범위를 확인하세요.")
        if not isinstance(self.duplicate_tolerance_seconds, (int, float)) or not 0 <= self.duplicate_tolerance_seconds <= 30:
            raise ValueError("중복 길이 허용값은 0~30초입니다.")
        for provider in (self.classify_provider, self.escalate_provider):
            if provider not in {"anthropic", "openai"}:
                raise ValueError("API 서비스를 확인하세요.")
        if not self.classify_model.strip() or not self.escalate_model.strip():
            raise ValueError("모델 ID를 입력하세요.")

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

    @staticmethod
    def backend():
        if os.name != "nt":
            raise RuntimeError("현재 버전의 키 보관은 Windows에서만 지원합니다.")
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def get(self, provider: str) -> str | None:
        return self.backend().get_password("music-sorter", provider)

    def set(self, provider: str, value: str) -> None:
        if not value.strip():
            raise ValueError("빈 키는 등록할 수 없습니다.")
        self.backend().set_password("music-sorter", provider, value.strip())

    def delete(self, provider: str) -> None:
        backend = self.backend()
        if backend.get_password("music-sorter", provider) is not None:
            backend.delete_password("music-sorter", provider)
