from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QApplication, QGroupBox, QMessageBox, QPushButton, QPlainTextEdit, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

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
        self.data_worker = None
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
        music.addRow("음악 폴더", root_row)
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(settings.include_subfolders)
        music.addRow(self.recursive)
        self.exclusions = QPlainTextEdit('\n'.join(settings.scan_exclude_folders))
        self.exclusions.setPlaceholderText('음악 폴더 아래 상대 폴더 · 한 줄에 하나 · 예: 개인 녹음')
        self.exclusions.setMaximumHeight(90)
        music.addRow('스캔 제외 폴더', self.exclusions)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 30)
        self.tolerance.setValue(settings.duplicate_tolerance_seconds)
        self.tolerance.setSuffix(" 초")
        music.addRow("같은 곡으로 볼 재생 시간 차이", self.tolerance)
        music.addRow(self.note("파일 내용으로 변경 여부를 확인합니다. 다른 위치로 연결된 폴더 바로가기는 제외합니다.\n분류와 작업 기록의 저장 위치는 데이터·복구 메뉴에서 변경할 수 있습니다."))
        database = QGroupBox('사용 중인 데이터베이스')
        database_layout = QVBoxLayout(database)
        self.db_path = self.storage_path(database_layout, 'DB 파일', library.path, library.path.parent)
        database_layout.addWidget(self.note('분류와 작업 기록을 보관하는 파일입니다.\n기본 위치는 Windows 사용자 앱 데이터이며 프로그램 업데이트 후에도 유지됩니다.'))
        music.addRow(database)

        api = self.page("AI / API 연결")
        api.addRow(self.note("키 등록·삭제는 즉시 자격 증명 저장소에 반영됩니다. 창 취소로 되돌리지 않습니다.\n연결 확인은 모델 목록만 조회합니다. 음악 전송·분류 요청은 하지 않습니다."))
        if CredentialStore.profile_keys is not None:
            api.addRow(self.note('명시적 개발 프로필/프로세스 환경 변수의 세션 키를 사용 중입니다.\n자격 증명 저장소의 키는 섞지 않습니다. 키 등록·삭제는 기본 모드에서 진행하세요.'))
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
            delete.setProperty('danger', True)
            delete.clicked.connect(lambda _, p=provider: self.delete_key(p))
            status = QLabel()
            status.setTextFormat(Qt.TextFormat.PlainText)
            self.key_states[provider] = status
            for widget in (show, save, delete, status):
                actions.addWidget(widget)
            api.addRow(controls)
            self.key_buttons[provider] = (save, delete)
            if CredentialStore.profile_keys is not None:
                save.setEnabled(False)
                delete.setEnabled(False)
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
                api.addRow(self.note('Last.fm 키는 음악 정보 보완 조회에서 사용합니다. API 오류는 해당 출처 조회 결과에 표시합니다.'))
            self.refresh_key(provider)
        self.workspace = QLineEdit(settings.anthropic_workspace_id)
        self.workspace.setPlaceholderText("선택 · 여러 워크스페이스용 Claude 키에 필요")
        self.workspace.textChanged.connect(lambda: self.invalidate_connection("anthropic"))
        api.addRow("Claude 워크스페이스 ID", self.workspace)

        classification = self.page("분류와 비용")
        self.providers, self.models = [], []
        for prefix, provider, model in (("처음 분류", settings.classify_provider, settings.classify_model),
                                         ("다시 분류", settings.escalate_provider, settings.escalate_model)):
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
        classification.addRow(self.note("AI / API 연결에서 불러온 모델을 선택하거나 모델 ID를 직접 입력하세요.\n연결 확인은 모델 목록 조회입니다. 실제 분류 가능 여부와 서비스 잔액은 실행할 때 확인합니다."))
        classification.addRow(self.note("음악 목록의 「AI로 분류」에서 곡·처리 방법·예산(미국 달러)을 선택합니다.\n가사는 기본적으로 보내지 않으며, 다시 분류할 때만 선택해서 보낼 수 있습니다.\n요금 정보를 확인하지 못한 모델은 실행할 수 없습니다. AI 결과는 틀릴 수 있어 확인이 필요합니다."))
        catalog = QPushButton('분류 이름 관리')
        catalog.clicked.connect(self.open_catalog)
        classification.addRow(catalog)
        self.advanced = {}
        for title, name, low, high in [('한 번에 AI로 보낼 곡 수', 'llm_tracks_per_request', 1, 20),
                                       ('곡당 AI 응답 길이 한도 (토큰)', 'llm_max_output_tokens_per_track', 256, 2000),
                                       ('AI 응답 대기 시간 (초)', 'llm_timeout_seconds', 10, 180),
                                       ('요청 한도 초과 시 추가 시도 횟수', 'llm_max_retries', 0, 3)]:
            control = QSpinBox()
            control.setRange(low, high)
            control.setValue(getattr(settings, name))
            self.advanced[name] = control
            classification.addRow(title, control)
        self.lyrics_default = QCheckBox('다시 분류할 때 저장된 가사를 보내도록 기본 선택')
        self.lyrics_default.setChecked(settings.include_lyrics_default)
        classification.addRow(self.lyrics_default)
        classification.addRow(self.note('AI 요청은 한 번에 하나씩 보냅니다. 응답이 없거나 서버 오류가 나면 자동으로 다시 보내지 않습니다.\n토큰은 AI 응답의 길이를 세는 단위입니다. 한도가 낮으면 결과가 잘릴 수 있습니다. 변경은 새 작업부터 적용합니다.'))

        external = self.page("인터넷 곡 정보")
        self.musicbrainz_enabled = QCheckBox('MusicBrainz 정보 조회')
        self.musicbrainz_enabled.setChecked(settings.musicbrainz_enabled)
        external.addRow(self.musicbrainz_enabled)
        self.musicbrainz_contact = QLineEdit(settings.musicbrainz_contact)
        self.musicbrainz_contact.setPlaceholderText('문의 받을 이메일 또는 웹사이트 주소')
        external.addRow('MusicBrainz 연락처', self.musicbrainz_contact)
        self.lastfm_enabled = QCheckBox('Last.fm 곡 정보·참고 태그 조회')
        self.lastfm_enabled.setChecked(settings.lastfm_enabled)
        external.addRow(self.lastfm_enabled)
        external.addRow(self.note('「곡 정보 찾기」에서 조회하며 찾은 정보를 앱에 저장해 다음 조회에 재사용합니다.\nMusicBrainz는 연락처가 없으면, Last.fm은 키가 없으면 건너뜁니다.\n제목·아티스트·버전이 다른 후보는 자동 연결하지 않습니다.'))
        output = self.page("파일 정리·재생목록")
        self.rollback_limit = QDoubleSpinBox()
        self.rollback_limit.setRange(.1, 10000)
        self.rollback_limit.setValue(settings.rollback_limit_gib)
        self.rollback_limit.setSuffix(' GiB')
        output.addRow('파일 정보 복구 자료 보관 한도', self.rollback_limit)
        self.playlist_format = QComboBox()
        self.playlist_format.addItem('UTF-8 M3U8 · PC 추천', 'm3u8')
        self.playlist_format.addItem('UTF-8 M3U · 기존 앱용', 'm3u')
        self.playlist_format.setCurrentIndex(self.playlist_format.findData(settings.playlist_format))
        output.addRow('재생목록 형식', self.playlist_format)
        output.addRow(self.note('곰오디오는 M3U8의 한글 경로·장르·재생을 확인했습니다.\nM3U는 곰오디오에서 한글 경로가 깨질 수 있습니다. 삼성 뮤직은 장치 검증 전입니다.\n형식 변경만으로 이전 목록을 지우거나 변경하지 않습니다. 다음 목록 생성부터 적용합니다.'))
        output.addRow(self.note("「파일 정리 미리보기」에서 폴더·파일 이름·장르 변경을 선택합니다.\n미리보기를 확인한 뒤 실제로 바꾸며 작업 기록에서 되돌릴 수 있습니다.\n재생목록은 음악 폴더와 함께 옮겨 사용할 수 있습니다.\n파일 복구 자료는 자동으로 지우지 않습니다. 분류·작업 기록은 하루 첫 변경 전에 백업하고 최근 7개를 보관합니다."))
        data = self.page("데이터·복구")
        storage = QGroupBox('사용자 데이터 위치')
        storage_layout = QVBoxLayout(storage)
        self.data_path = self.storage_path(storage_layout, '데이터 폴더', config_path.parent, config_path.parent)
        storage_layout.addWidget(self.note('설정·분류·작업 기록과 백업·복구 자료를 보관합니다.\n백업에는 음악 파일을 포함하지 않습니다. 저장 위치를 바꾸려면 아래 「새 폴더로 데이터 복사」를 이용하세요.'))
        data.addRow(storage)
        backup = QPushButton("분류·작업 기록 백업")
        backup.clicked.connect(self.backup)
        data.addRow(backup)
        self.data_buttons = []
        for title, action in (('백업에서 분류·작업 기록 복원', self.restore_database), ('새 폴더로 데이터 복사', self.migrate_data),
                              ('복사한 폴더를 다음 실행부터 사용', self.activate_data),
                              ('개인정보를 뺀 설정 내보내기', self.export_preferences), ('.env의 설정·키 가져오기', self.import_profile)):
            button = QPushButton(title)
            button.clicked.connect(action)
            data.addRow(button)
            self.data_buttons.append(button)
        if CredentialStore.profile_keys is not None:
            self.data_buttons[-1].setEnabled(False)
        self.data_status = self.note('음악 파일 변경을 되돌리려면 작업 기록에서 파일 정리를 두 번 클릭하세요.\n여기서는 저장된 분류·작업 기록만 복원합니다. 복원 후 음악 폴더를 다시 스캔해야 합니다. 데이터 복사는 기존 폴더를 보존합니다.')
        data.addRow(self.data_status)
        with library.connection() as db:
            cache = db.execute('SELECT count(*) FROM external_cache').fetchone()[0]
            responses = db.execute('SELECT count(*) FROM llm_cache').fetchone()[0]
        data.addRow(self.note(f'저장된 인터넷 곡 정보 {cache:,}건 · 저장된 AI 응답 {responses:,}건\n작업 결과와 오류는 작업 이력에서 확인할 수 있습니다. 키·API 오류 원문은 이력에 저장하지 않습니다.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Ok)
        self.buttons = buttons
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        confirm.setProperty('primary', True)
        confirm.setDefault(True)
        confirm.setToolTip('설정을 저장하고 창을 닫습니다.')
        self.apply_button.setProperty('applyAction', True)
        self.apply_button.setToolTip('설정을 저장하고 창은 계속 열어 둡니다.')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip('아직 적용하지 않은 설정 변경을 취소합니다. 이미 적용한 설정과 등록한 키는 유지됩니다.')
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.save)
        buttons.accepted.connect(lambda: self.accept() if self.save() else None)
        layout.addWidget(buttons)
        self.menu.setCurrentRow(0)
        for control in self.findChildren(QLineEdit):
            if not control.isReadOnly():
                control.textChanged.connect(self.update_apply_state)
        for control in self.findChildren(QComboBox):
            control.currentTextChanged.connect(self.update_apply_state)
        for control in (*self.findChildren(QSpinBox), *self.findChildren(QDoubleSpinBox)):
            control.valueChanged.connect(self.update_apply_state)
        for control in self.findChildren(QCheckBox):
            control.toggled.connect(self.update_apply_state)
        self.exclusions.textChanged.connect(self.update_apply_state)
        self.update_apply_state()

    def update_apply_state(self, *_):
        self.apply_button.setEnabled(self.draft() != self.settings and not self.connection_busy())

    def storage_path(self, layout, title, path, folder):
        layout.addWidget(QLabel(title))
        edit = QLineEdit(str(path.resolve()))
        edit.setReadOnly(True)
        edit.setProperty('storagePath', True)
        edit.setAccessibleName(title)
        edit.setToolTip(edit.text())
        edit.setCursorPosition(0)
        layout.addWidget(edit)
        actions = QHBoxLayout()
        open_button = QPushButton('폴더 열기')
        open_button.setAccessibleName(f'{title} 폴더 열기')
        open_button.clicked.connect(lambda: self.open_storage_folder(folder))
        copy_button = QPushButton('경로 복사')
        copy_button.setAccessibleName(f'{title} 경로 복사')
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(edit.text()))
        actions.addWidget(open_button)
        actions.addWidget(copy_button)
        actions.addStretch()
        layout.addLayout(actions)
        return edit

    def open_storage_folder(self, path):
        if not path.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            QMessageBox.warning(self, '폴더 열기 실패', '데이터 폴더 접근 상태를 확인하세요.')

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
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.menu.addItem(title)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        self.pages.addWidget(scroll)
        return layout

    def choose_root(self):
        selected = QFileDialog.getExistingDirectory(self, "음악 폴더 선택", self.root.text())
        if selected:
            self.root.setText(selected)

    def open_catalog(self):
        if self.connection_busy():
            return
        from .catalog_dialog import CatalogDialog
        CatalogDialog(self.library, self).exec()

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
        return self.connection_worker is not None or self.data_worker is not None

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
        self.update_apply_state()
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
        self.update_apply_state()
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
                          scan_exclude_folders=list(dict.fromkeys(line.strip() for line in self.exclusions.toPlainText().splitlines() if line.strip())),
                          notify_on_completion=self.notify.isChecked(), duplicate_tolerance_seconds=self.tolerance.value(),
                          classify_provider=self.providers[0].currentData(), classify_model=self.models[0].currentText().strip(),
                          escalate_provider=self.providers[1].currentData(), escalate_model=self.models[1].currentText().strip(),
                          anthropic_workspace_id=self.workspace.text().strip(),
                          musicbrainz_enabled=self.musicbrainz_enabled.isChecked(), musicbrainz_contact=self.musicbrainz_contact.text().strip(),
                          lastfm_enabled=self.lastfm_enabled.isChecked(), rollback_limit_gib=self.rollback_limit.value(),
                          playlist_format=self.playlist_format.currentData(),
                          include_lyrics_default=self.lyrics_default.isChecked(), **{name: control.value() for name, control in self.advanced.items()})

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
        if self.connection_busy():
            return False
        updated = self.draft()
        try:
            if updated.music_root:
                if not Path(updated.music_root).is_dir():
                    raise ValueError("음악 폴더에 접근할 수 없습니다.")
                updated.validate()
                self.library.bind_root(Path(updated.music_root))
            updated.save(self.config_path)
            self.settings = updated
            self.update_apply_state()
            self.settings_saved.emit(updated)
            return True
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "설정 저장 보류", str(error))
            return False

    def backup(self):
        if self.connection_busy():
            return
        name = now().replace(":", "-").replace("+", "_") + ".sqlite3"
        try:
            self.library.backup(self.config_path.parent / "backups" / name)
            QMessageBox.information(self, "분류·작업 기록 백업 완료", "분류와 작업 기록을 백업하고 읽을 수 있는지 확인했습니다. 음악 파일은 백업에 포함하지 않습니다.")
        except Exception:
            QMessageBox.warning(self, "백업 실패", "백업 폴더의 접근 권한과 저장 공간을 확인하세요.")

    def data_start(self, action, callback):
        if self.connection_busy():
            return
        from .operations import OperationWorker
        self.data_worker = OperationWorker(lambda *_: action(), self)
        self.update_apply_state()
        self.data_worker.result.connect(callback)
        self.data_worker.error.connect(self.data_status.setText)
        self.data_worker.finished.connect(self.data_finished)
        for button in self.data_buttons:
            button.setEnabled(False)
        self.data_status.setText('저장된 기록과 복구 자료를 읽을 수 있는지 확인하고 있습니다. 완료 후 결과를 표시합니다.')
        self.data_worker.start()

    def data_finished(self):
        worker, self.data_worker = self.data_worker, None
        self.update_apply_state()
        worker.deleteLater()
        for button in self.data_buttons:
            button.setEnabled(True)
        if CredentialStore.profile_keys is not None:
            self.data_buttons[-1].setEnabled(False)

    def restore_database(self):
        if self.connection_busy():
            return
        path, _ = QFileDialog.getOpenFileName(self, '분류·작업 기록 백업 선택', str(self.config_path.parent / 'backups'), '백업 파일 (*.sqlite3 *.db)')
        if not path:
            return
        from ..maintenance import prepare_restore
        self.data_start(lambda: prepare_restore(self.library, Path(path)), self.restore_prepared)

    def restore_prepared(self, plan):
        # Ask after the preparation thread has fully released its resources.
        from PySide6.QtCore import QTimer
        def confirm():
            report = plan['report']
            text = f"백업에 등록된 곡: {report['tracks']:,}곡\n현재 분류와 작업 기록은 별도로 백업한 뒤 교체합니다. 음악 파일은 그대로 두며 복원 후 음악 폴더를 다시 스캔해야 합니다.\n이 백업으로 복원할까요?"
            if QMessageBox.question(self, '분류·작업 기록 복원', text, defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                self.data_status.setText('복원용 사본을 보존했습니다. 현재 분류와 작업 기록은 바뀌지 않았습니다.')
                return
            from ..maintenance import apply_restore
            def done(result):
                self.data_status.setText(f"{result['tracks']:,}곡의 기록을 복원했습니다. 음악 폴더를 다시 스캔한 뒤 파일 정리·AI 분류를 실행하세요. 이전 기록의 백업은 보존했습니다.")
                if hasattr(self.parent(), 'refresh'):
                    self.parent().refresh()
            self.data_start(lambda: apply_restore(self.library, plan), done)
        QTimer.singleShot(0, confirm)

    def migrate_data(self):
        if self.connection_busy():
            return
        path = QFileDialog.getExistingDirectory(self, '데이터를 복사할 비어 있는 폴더 선택')
        if not path:
            return
        from ..maintenance import migrate_copy
        def done(result):
            self.data_status.setText(f"{result['report']['tracks']:,}곡의 기록과 복구 자료 {result['rollback_files']:,}개를 확인해 복사했습니다.\n새 위치: {result['destination']}\n「복사한 폴더를 다음 실행부터 사용」을 누르면 앱을 다시 열 때 새 위치를 사용합니다.")
        self.data_start(lambda: migrate_copy(self.library, self.settings, Path(path)), done)

    def activate_data(self):
        if self.connection_busy():
            return
        path = QFileDialog.getExistingDirectory(self, '앞에서 데이터를 복사한 폴더 선택')
        if not path:
            return
        if QMessageBox.question(self, '다음 실행 데이터 위치', f'앱을 다시 열 때 다음 폴더의 데이터를 사용할까요?\n{path}\n기존 분류·작업 기록과 복구 자료는 보존합니다. 지금은 기존 폴더를 계속 사용합니다.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from ..settings import activate_data_directory
        self.data_start(lambda: activate_data_directory(path), lambda _: self.data_status.setText('검증한 데이터 위치를 저장했습니다. 앱을 닫고 다시 열면 새 위치를 사용합니다.'))

    def export_preferences(self):
        if self.connection_busy():
            return
        if self.connection_busy():
            return
        path, _ = QFileDialog.getSaveFileName(self, '개인정보 제외 설정 내보내기', 'music-sorter-settings.json', 'JSON (*.json)')
        if path:
            from ..maintenance import export_settings
            try:
                export_settings(self.draft(), Path(path))
                self.data_status.setText('키·음악 경로·연락처·워크스페이스 식별자를 제외한 일반 설정을 내보냈습니다.')
            except OSError:
                self.data_status.setText('설정 출력 경로·파일 접근 상태를 확인하세요.')

    def import_profile(self):
        if self.connection_busy() or CredentialStore.profile_keys is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, '.env 설정·키 가져오기 (파일 값만 사용)', '', '환경 설정 (*.env);;모든 파일 (*)')
        if not path:
            return
        from ..profiles import load_profile
        try:
            profile = load_profile(path, self.settings, environment={})
            text = '등록할 키 종류: ' + (', '.join(profile.keys) or '없음') + '\n지원하지 않는 항목: ' + (', '.join(profile.ignored) or '없음') + '\n일반 설정은 로컬에, 키는 Windows 자격 증명 저장소에 가져옵니다. 원본 파일은 보존합니다. 진행할까요?'
            if QMessageBox.question(self, '프로필 가져오기', text, defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            if profile.settings.music_root:
                if not Path(profile.settings.music_root).is_dir():
                    raise ValueError('음악 폴더 폴더를 찾을 수 없습니다.')
                self.library.bind_root(Path(profile.settings.music_root))
            for provider, key in profile.keys.items():
                self.vault.set(provider, key)
            profile.settings.save(self.config_path)
            self.settings = profile.settings
            self.settings_saved.emit(profile.settings)
            QMessageBox.information(self, '가져오기 완료', '일반 설정·키를 저장했습니다. 이 설정 창을 다시 열어 확인하세요. 원본 .env는 보존했습니다.')
            self.accept()
        except Exception:
            for provider in self.key_states:
                self.refresh_key(provider)
            QMessageBox.warning(self, '가져오기 보류', '프로필 형식·음악 폴더·자격 증명 저장소를 확인하세요. 일부 키가 먼저 등록됐다면 유지되며 원본 프로필은 보존했습니다.')
