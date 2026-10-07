from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ..database import Library, now
from ..settings import CredentialStore, Settings


class SettingsDialog(QDialog):
    settings_saved = Signal(object)

    def __init__(self, settings: Settings, config_path: Path, library: Library, parent=None):
        super().__init__(parent)
        self.settings, self.config_path, self.library = settings, config_path, library
        self.vault = CredentialStore()
        self.setWindowTitle("설정 · music-sorter")
        self.resize(850, 640)
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self.menu = QListWidget()
        self.menu.setFixedWidth(190)
        self.pages = QStackedWidget()
        body.addWidget(self.menu)
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)
        self.menu.currentRowChanged.connect(self.pages.setCurrentIndex)

        general = self.page("일반")
        self.theme = QComboBox()
        for title, value in (("시스템", "system"), ("밝게", "light"), ("어둡게", "dark")):
            self.theme.addItem(title, value)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        general.addRow("테마", self.theme)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.8, 2)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(settings.font_scale)
        general.addRow("글자 크기 배율", self.scale)
        self.notify = QCheckBox("작업 완료 알림 표시")
        self.notify.setChecked(settings.notify_on_completion)
        general.addRow(self.notify)
        general.addRow(self.note("종료 시 스캔을 안전하게 중단하고 기록을 저장합니다. 새 유료 작업·파일 적용을 자동 재개하지 않습니다."))

        music = self.page("음악 라이브러리")
        self.root = QLineEdit(settings.music_root)
        self.root.setReadOnly(True)
        root_row = QWidget()
        row = QHBoxLayout(root_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.root, 1)
        choose = QPushButton("폴더 선택")
        choose.clicked.connect(self.choose_root)
        row.addWidget(choose)
        music.addRow("음악 루트", root_row)
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(settings.include_subfolders)
        music.addRow(self.recursive)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 30)
        self.tolerance.setValue(settings.duplicate_tolerance_seconds)
        self.tolerance.setSuffix(" 초")
        music.addRow("중복 길이 허용값", self.tolerance)
        music.addRow(self.note("전체 파일 SHA-256 검증 · 정션/심볼릭 링크 제외\n등록 후 음악 루트 교체·DB 이관은 후속 기능입니다."))
        music.addRow(self.note(f"DB: {library.path}"))

        api = self.page("LLM / API 연결")
        api.addRow(self.note("키 등록·삭제는 즉시 로컬 자격 증명 저장소에 반영됩니다. 창 취소로 되돌리지 않습니다.\nAPI 연결 확인·모델 조회·실제 분류는 2단계에서 제공됩니다."))
        self.key_edits = {}
        self.key_states = {}
        for provider, title in (("openai", "OpenAI"), ("anthropic", "Claude"), ("lastfm", "Last.fm")):
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("새 키 입력 · 기존 키를 표시하지 않음")
            self.key_edits[provider] = key
            api.addRow(title, key)
            controls = QWidget()
            actions = QHBoxLayout(controls)
            actions.setContentsMargins(0, 0, 0, 0)
            show = QCheckBox("입력값 표시")
            show.toggled.connect(lambda checked, edit=key: edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
            save = QPushButton("등록 / 교체")
            save.clicked.connect(lambda _, p=provider: self.save_key(p))
            delete = QPushButton("삭제")
            delete.clicked.connect(lambda _, p=provider: self.delete_key(p))
            status = QLabel()
            self.key_states[provider] = status
            for widget in (show, save, delete, status):
                actions.addWidget(widget)
            api.addRow(controls)
            self.refresh_key(provider)

        classification = self.page("분류와 비용")
        self.providers, self.models = [], []
        for prefix, provider, model in (("1차", settings.classify_provider, settings.classify_model),
                                         ("재판정", settings.escalate_provider, settings.escalate_model)):
            combo = QComboBox()
            combo.addItem("Claude", "anthropic")
            combo.addItem("OpenAI", "openai")
            combo.setCurrentIndex(combo.findData(provider))
            edit = QLineEdit(model)
            self.providers.append(combo)
            self.models.append(edit)
            classification.addRow(f"{prefix} 서비스", combo)
            classification.addRow(f"{prefix} 모델 ID", edit)
        classification.addRow(self.note("선택은 저장할 수 있습니다. 현재는 유료 요청을 실행하지 않습니다.\n작업별 예산·Batch·재판정·가사·신뢰도 설정은 API 구현 단계에서 제공됩니다."))

        external = self.page("외부 음악 정보")
        external.addRow(self.note("MusicBrainz·Last.fm 조회는 2단계에서 제공합니다.\nLast.fm 키는 API 연결 메뉴에서 등록할 수 있습니다."))
        output = self.page("파일 정리·재생목록")
        output.addRow(self.note("현재 단계는 DB 판정만 저장합니다. 음악 파일을 이동하거나 태그를 기록하지 않습니다.\n미리보기·적용·복구·재생목록은 후속 단계에서 제공합니다."))
        data = self.page("데이터·복구")
        data.addRow(self.note(f"사용자 데이터: {config_path.parent}\n백업은 DB 사본이며 음악 파일을 포함하지 않습니다."))
        backup = QPushButton("검증된 DB 백업 만들기")
        backup.clicked.connect(self.backup)
        data.addRow(backup)
        data.addRow(self.note("DB 복원·데이터 이관·음악 파일 복구는 후속 단계에서 제공합니다."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.save)
        buttons.accepted.connect(lambda: self.accept() if self.save() else None)
        layout.addWidget(buttons)
        self.menu.setCurrentRow(0)

    @staticmethod
    def note(text):
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def page(self, title):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.menu.addItem(title)
        self.pages.addWidget(widget)
        return layout

    def choose_root(self):
        selected = QFileDialog.getExistingDirectory(self, "음악 루트 선택", self.root.text())
        if selected:
            self.root.setText(selected)

    def refresh_key(self, provider):
        try:
            self.key_states[provider].setText("등록됨 · 미확인" if self.vault.get(provider) else "미등록")
        except Exception:
            self.key_states[provider].setText("저장소 접근 오류")

    def save_key(self, provider):
        try:
            self.vault.set(provider, self.key_edits[provider].text())
            self.key_edits[provider].clear()
            self.refresh_key(provider)
        except Exception:
            QMessageBox.warning(self, "키 저장 보류", "키 입력과 Windows 자격 증명 저장소를 확인하세요. 평문 파일에 저장하지 않았습니다.")

    def delete_key(self, provider):
        if QMessageBox.question(self, "키 삭제", "등록한 키를 자격 증명 저장소에서 삭제할까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.vault.delete(provider)
            self.key_edits[provider].clear()
            self.refresh_key(provider)
        except Exception:
            QMessageBox.warning(self, "키 삭제 보류", "Windows 자격 증명 저장소에 접근할 수 없습니다.")

    def draft(self):
        return replace(self.settings, music_root=self.root.text(), theme=self.theme.currentData(),
                          font_scale=self.scale.value(), include_subfolders=self.recursive.isChecked(),
                          notify_on_completion=self.notify.isChecked(), duplicate_tolerance_seconds=self.tolerance.value(),
                          classify_provider=self.providers[0].currentData(), classify_model=self.models[0].text().strip(),
                          escalate_provider=self.providers[1].currentData(), escalate_model=self.models[1].text().strip())

    def reject(self):
        unsaved = self.draft() != self.settings or any(edit.text() for edit in self.key_edits.values())
        if unsaved and QMessageBox.question(self, "미저장 설정", "저장하지 않은 설정·입력 키를 버릴까요? 이미 등록·삭제한 키는 유지됩니다.") != QMessageBox.StandardButton.Yes:
            return
        for edit in self.key_edits.values():
            edit.clear()
        super().reject()

    def save(self) -> bool:
        updated = self.draft()
        try:
            if updated.music_root:
                if not Path(updated.music_root).is_dir():
                    raise ValueError("음악 폴더에 접근할 수 없습니다.")
                updated.validate()
                self.library.bind_root(Path(updated.music_root))
            updated.save(self.config_path)
            self.settings = updated
            self.settings_saved.emit(updated)
            return True
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "설정 저장 보류", str(error))
            return False

    def backup(self):
        name = now().replace(":", "-").replace("+", "_") + ".sqlite3"
        try:
            self.library.backup(self.config_path.parent / "backups" / name)
            QMessageBox.information(self, "DB 백업 완료", "일관된 DB 사본의 무결성을 확인했습니다.")
        except Exception:
            QMessageBox.warning(self, "백업 보류", "백업 경로·공간·DB 상태를 확인하세요.")
