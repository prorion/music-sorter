from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..database import Library, now
from ..settings import CredentialStore, Settings
from .connection_worker import ConnectionWorker


class SettingsDialog(QDialog):
    settings_saved = Signal(object)

    def __init__(self, settings: Settings, config_path: Path, library: Library, parent=None):
        super().__init__(parent)
        self.settings, self.config_path, self.library = settings, config_path, library
        self.vault = CredentialStore()
        self.connection_worker = None
        self.model_lists = {}
        self.setWindowTitle("설정 · music-sorter")
        self.resize(850, 640)
        self.setMinimumSize(780, 600)
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self.menu = QListWidget()
        self.menu.setObjectName("navigation")
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
        api.addRow(self.note("키 등록·삭제는 즉시 자격 증명 저장소에 반영됩니다. 창 취소로 되돌리지 않습니다.\n연결 확인은 모델 목록만 조회합니다. 음악 전송·분류 요청은 하지 않습니다."))
        self.key_edits = {}
        self.key_states = {}
        self.connection_buttons = {}
        self.key_buttons = {}
        self.connection_details = {}
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
            status.setTextFormat(Qt.TextFormat.PlainText)
            self.key_states[provider] = status
            for widget in (show, save, delete, status):
                actions.addWidget(widget)
            api.addRow(controls)
            self.key_buttons[provider] = (save, delete)
            if provider in {"openai", "anthropic"}:
                connect = QPushButton("연결 확인 / 모델 조회")
                connect.clicked.connect(lambda _, p=provider: self.check_connection(p))
                self.connection_buttons[provider] = connect
                api.addRow(connect)
                detail = self.note("확인 결과는 이 창에서만 유지됩니다.")
                detail.setTextFormat(Qt.TextFormat.PlainText)
                self.connection_details[provider] = detail
                api.addRow(detail)
            else:
                api.addRow(self.note("Last.fm 연결 확인·음악 정보 조회는 후속 기능입니다."))
            self.refresh_key(provider)
        self.workspace = QLineEdit(settings.anthropic_workspace_id)
        self.workspace.setPlaceholderText("선택 · 여러 워크스페이스용 Claude 키에 필요")
        self.workspace.textChanged.connect(lambda: self.invalidate_connection("anthropic"))
        api.addRow("Claude 워크스페이스 ID", self.workspace)

        classification = self.page("분류와 비용")
        self.providers, self.models = [], []
        for prefix, provider, model in (("1차", settings.classify_provider, settings.classify_model),
                                         ("재판정", settings.escalate_provider, settings.escalate_model)):
            combo = QComboBox()
            combo.addItem("Claude", "anthropic")
            combo.addItem("OpenAI", "openai")
            combo.setCurrentIndex(combo.findData(provider))
            edit = QComboBox()
            edit.setEditable(True)
            edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            edit.setEditText(model)
            self.providers.append(combo)
            self.models.append(edit)
            classification.addRow(f"{prefix} 서비스", combo)
            classification.addRow(f"{prefix} 모델 ID", edit)
            combo.currentIndexChanged.connect(lambda _, i=len(self.providers) - 1: self.refresh_models(i))
        classification.addRow(self.note("API 연결 메뉴에서 조회한 모델을 목록에서 선택하거나 ID를 직접 입력하세요.\n목록 조회 성공은 선택 모델의 분류·구조화 출력·Batch·잔액 확인을 뜻하지 않습니다."))
        classification.addRow(self.note("선택은 저장할 수 있습니다. 현재는 유료 요청을 실행하지 않습니다.\n작업별 예산·Batch·재판정·가사·신뢰도 설정은 API 구현 단계에서 제공됩니다."))

        external = self.page("외부 음악 정보")
        external.addRow(self.note("MusicBrainz·Last.fm 조회는 2단계에서 제공합니다.\nLast.fm 키는 API 연결 메뉴에서 등록할 수 있습니다."))
        output = self.page("파일 정리·재생목록")
        output.addRow(self.note("라이브러리의 파일 정리 미리보기에서 폴더·이름·장르 기록을 각각 선택합니다.\n실제 적용은 미리보기 확인 뒤 실행하며 작업 이력에서 되돌릴 수 있습니다.\n재생목록 메뉴에서 기본 목록과 조건 조합 목록을 생성합니다. UTF-8·CRLF·상대 경로를 사용합니다.\n복구 자료 보관 한도는 10GiB, 자동 DB 백업은 하루 첫 적용 전 생성하고 최근 7개를 보관합니다."))
        data = self.page("데이터·복구")
        data.addRow(self.note(f"사용자 데이터: {config_path.parent}\n백업은 DB 사본이며 음악 파일을 포함하지 않습니다."))
        backup = QPushButton("검증된 DB 백업 만들기")
        backup.clicked.connect(self.backup)
        data.addRow(backup)
        data.addRow(self.note("음악 파일 복구: 작업 이력의 파일 정리 항목을 두 번 클릭하고 되돌리기를 선택하세요.\nDB 전체 복원·데이터 위치 이관은 후속 단계에서 제공합니다."))
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
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        self.pages.addWidget(scroll)
        return layout

    def choose_root(self):
        selected = QFileDialog.getExistingDirectory(self, "음악 루트 선택", self.root.text())
        if selected:
            self.root.setText(selected)

    def refresh_key(self, provider):
        try:
            registered = bool(self.vault.get(provider))
            self.key_states[provider].setText("등록됨 · 미확인" if registered else "미등록")
            if provider in self.connection_buttons:
                self.connection_buttons[provider].setEnabled(registered)
        except Exception:
            self.key_states[provider].setText("저장소 접근 오류")
            if provider in self.connection_buttons:
                self.connection_buttons[provider].setEnabled(False)

    def invalidate_connection(self, provider):
        self.model_lists.pop(provider, None)
        self.refresh_key(provider)
        if provider in self.connection_details:
            self.connection_details[provider].setText("확인 결과는 이 창에서만 유지됩니다.")
        if hasattr(self, "providers"):
            for index, combo in enumerate(self.providers):
                if combo.currentData() == provider:
                    self.refresh_models(index)

    def refresh_models(self, index):
        combo = self.models[index]
        text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for model in self.model_lists.get(self.providers[index].currentData(), ()):
            combo.addItem(model.id)
            combo.setItemData(combo.count() - 1, model.name, Qt.ItemDataRole.ToolTipRole)
        combo.setEditText(text)
        combo.blockSignals(False)

    def connection_busy(self):
        return self.connection_worker is not None

    def check_connection(self, provider):
        if self.connection_busy():
            return
        if self.key_edits[provider].text():
            self.connection_details[provider].setText("입력 중인 키는 미등록입니다. 먼저 등록 / 교체를 눌러 저장하세요.")
            return
        self.invalidate_connection(provider)
        self.key_states[provider].setText("연결 확인 중…")
        self.connection_details[provider].setText("모델 목록 조회 중 · 설정 창 종료는 조회 완료 후 가능합니다.")
        for button in self.connection_buttons.values():
            button.setEnabled(False)
        for buttons in self.key_buttons.values():
            for button in buttons:
                button.setEnabled(False)
        self.workspace.setEnabled(False)
        worker = ConnectionWorker(provider, self.vault, self.workspace.text().strip(), self)
        self.connection_worker = worker
        worker.finished.connect(self.connection_finished)
        worker.start()

    def connection_finished(self):
        worker = self.connection_worker
        provider = worker.provider
        for buttons in self.key_buttons.values():
            for button in buttons:
                button.setEnabled(True)
        self.workspace.setEnabled(True)
        for service in self.connection_buttons:
            # Preserve the other service's displayed verification status.
            try:
                self.connection_buttons[service].setEnabled(bool(self.vault.get(service)))
            except Exception:
                self.connection_buttons[service].setEnabled(False)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        if worker.outcome is None:
            self.key_states[provider].setText("연결 오류")
            self.connection_details[provider].setText(f"{worker.message}\n확인 시각: {stamp}")
        else:
            self.model_lists[provider] = worker.outcome.models
            self.key_states[provider].setText("연결 확인됨")
            self.connection_details[provider].setText(f"모델 {len(worker.outcome.models)}개 조회 · {stamp}\n분류 실행 권한·잔액은 별도 확인이 필요합니다.")
        for index in range(len(self.providers)):
            self.refresh_models(index)
        self.connection_worker = None
        worker.deleteLater()

    def save_key(self, provider):
        if self.connection_busy():
            return
        try:
            self.vault.set(provider, self.key_edits[provider].text())
            self.key_edits[provider].clear()
            self.invalidate_connection(provider)
        except Exception:
            QMessageBox.warning(self, "키 저장 보류", "키 입력과 Windows 자격 증명 저장소를 확인하세요. 평문 파일에 저장하지 않았습니다.")

    def delete_key(self, provider):
        if self.connection_busy():
            return
        if QMessageBox.question(self, "키 삭제", "등록한 키를 자격 증명 저장소에서 삭제할까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.vault.delete(provider)
            self.key_edits[provider].clear()
            self.invalidate_connection(provider)
        except Exception:
            QMessageBox.warning(self, "키 삭제 보류", "Windows 자격 증명 저장소에 접근할 수 없습니다.")

    def draft(self):
        return replace(self.settings, music_root=self.root.text(), theme=self.theme.currentData(),
                          font_scale=self.scale.value(), include_subfolders=self.recursive.isChecked(),
                          notify_on_completion=self.notify.isChecked(), duplicate_tolerance_seconds=self.tolerance.value(),
                          classify_provider=self.providers[0].currentData(), classify_model=self.models[0].currentText().strip(),
                          escalate_provider=self.providers[1].currentData(), escalate_model=self.models[1].currentText().strip(),
                          anthropic_workspace_id=self.workspace.text().strip())

    def done(self, result):
        if self.connection_busy():
            return
        super().done(result)

    def closeEvent(self, event):
        if self.connection_busy():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self.connection_busy():
            return
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
