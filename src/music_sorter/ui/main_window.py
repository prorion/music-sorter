from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                              QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                              QScrollArea, QSplitter, QStackedWidget, QTableView, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget)

from ..classification import TAXONOMY
from ..database import Library
from ..scanner import ScanControl, scan_library
from ..settings import Settings
from .duplicates import DuplicateDialog
from .editor import TrackEditor
from .player import Player
from .settings_dialog import SettingsDialog
from .theme import apply_theme

STATE_LABELS = {"ready": "확인됨", "external_change": "외부 변경", "missing": "누락", "unavailable": "확인 불가",
                "link_pending": "연결 보류", "replaced": "교체된 기록", "unclassified": "미분류", "unresolved": "미확정",
                "confirmed": "확정", "running": "진행 중", "completed": "완료", "cancelled": "취소",
                "partial": "부분 완료", "failed": "실패", "interrupted": "이전 실행 중단"}


class TrackModel(QAbstractTableModel):
    headers = ["제목", "아티스트", "대분류", "세부 장르", "분류 상태", "파일 상태"]
    order_requested = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return track["path"]
        if role == Qt.ItemDataRole.DisplayRole:
            classification = track["classification"]
            values = [track["title"], track["artist"], classification["major"]["value"] or "—",
                      " · ".join(classification["subgenre"]["value"] or []),
                      STATE_LABELS[track["review_state"]], STATE_LABELS.get(track["file_state"], track["file_state"])]
            return values[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        mapping = {0: "title_key", 1: "artist_key", 2: "major", 3: "subgenre", 4: "review_state", 5: "file_state"}
        if column in mapping:
            self.order_requested.emit(mapping[column], order == Qt.SortOrder.DescendingOrder)


class ScanWorker(QThread):
    progress = Signal(int, int)
    result = Signal(dict)
    error = Signal(str)

    def __init__(self, library, root, recursive, parent=None):
        super().__init__(parent)
        self.library, self.root, self.recursive = library, root, recursive
        self.control = ScanControl()

    def run(self):
        try:
            result = scan_library(self.library, self.root, self.recursive, self.control,
                                  lambda count, failed: self.progress.emit(count, failed))
            self.result.emit(result)
        except Exception as error:
            self.error.emit(type(error).__name__)


class MainWindow(QMainWindow):
    def __init__(self, library: Library, settings: Settings, config_path: Path):
        super().__init__()
        self.library, self.settings, self.config_path = library, settings, config_path
        self.worker = None
        self.offset = 0
        self.page_size = 200
        self.sort_column, self.descending = "title_key", False
        self.refreshing = False
        self.active_navigation = 0
        self.applied_filters = ("", "", "")
        self.setWindowTitle("music-sorter · 로컬 관리 0.1")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 700)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        top = QHBoxLayout()
        brand = QLabel("music-sorter")
        brand.setStyleSheet("font-size: 24px; font-weight: 700;")
        top.addWidget(brand)
        self.root_label = QLabel()
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.root_label, 1)
        settings_button = QPushButton("⚙ 설정")
        settings_button.setAccessibleName("설정 열기")
        settings_button.setToolTip("API 키 · 모델 · 화면 · 음악 라이브러리")
        settings_button.clicked.connect(self.open_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.addItems(["음악 라이브러리", "분류 검토", "중복 검토", "재생목록", "작업 이력"])
        self.navigation.setFixedWidth(180)
        body.addWidget(self.navigation)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)

        library_page = QWidget()
        main = QVBoxLayout(library_page)
        main.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("제목 또는 아티스트 검색")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)
        self.major_filter = QComboBox()
        self.major_filter.addItem("모든 대분류", "")
        for name in TAXONOMY["major"]:
            self.major_filter.addItem(name, name)
        filters.addWidget(self.major_filter)
        self.state_filter = QComboBox()
        self.state_filter.addItem("모든 상태", "")
        for state in ("unclassified", "unresolved", "confirmed", "external_change", "missing", "link_pending", "unavailable"):
            self.state_filter.addItem(STATE_LABELS[state], state)
        filters.addWidget(self.state_filter)
        main.addLayout(filters)
        actions = QHBoxLayout()
        self.scan_button = QPushButton("폴더 등록 / 스캔")
        self.scan_button.setProperty("primary", True)
        self.scan_button.clicked.connect(self.start_scan)
        actions.addWidget(self.scan_button)
        for title, tooltip in (("분류 실행", "외부 API·LLM은 다음 개발 단계에서 제공합니다"),
                               ("파일 정리 미리보기", "파일 적용·복구 검증 후 제공합니다")):
            button = QPushButton(title)
            button.setEnabled(False)
            button.setToolTip(tooltip)
            actions.addWidget(button)
        details = QPushButton("상세 접기 / 펼치기")
        details.clicked.connect(lambda: self.detail_panel.setVisible(not self.detail_panel.isVisible()))
        actions.addWidget(details)
        actions.addStretch()
        main.addLayout(actions)
        self.splitter = QSplitter()
        self.model = TrackModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 140)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.model.order_requested.connect(self.order_changed)
        self.table.selectionModel().currentRowChanged.connect(self.selection_changed)
        self.splitter.addWidget(self.table)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setMinimumWidth(320)
        self.editor = TrackEditor(library)
        self.editor.saved.connect(self.refresh)
        self.editor.external_review.connect(self.review_external)
        self.detail_scroll.setWidget(self.editor)
        self.detail_panel = QWidget()
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.detail_scroll, 1)
        detail_layout.addWidget(self.editor.save_button)
        self.player = Player()
        detail_layout.addWidget(self.player)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setSizes([700, 360])
        main.addWidget(self.splitter, 1)
        page_controls = QHBoxLayout()
        self.previous = QPushButton("이전 페이지")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next = QPushButton("다음 페이지")
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.count_label = QLabel()
        page_controls.addWidget(self.previous)
        page_controls.addWidget(self.count_label, 1)
        page_controls.addWidget(self.next)
        main.addLayout(page_controls)
        self.pages.addWidget(library_page)

        playlist_page = QWidget()
        playlist_layout = QVBoxLayout(playlist_page)
        playlist_layout.addWidget(QLabel("재생목록 생성은 파일 적용·복구 구현 후 연결합니다.\n현재 단계에서는 음악 파일과 기존 재생목록을 변경하지 않습니다."))
        playlist_layout.addStretch()
        self.pages.addWidget(playlist_page)
        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(["작업", "상태", "읽은 파일", "실패", "시작 시각 (UTC)"])
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.horizontalHeader().setStretchLastSection(True)
        self.pages.addWidget(self.jobs_table)
        footer = QHBoxLayout()
        self.status = QLabel("로컬 관리 · API 비용 없음 · 파일 변경 없음")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(180)
        footer.addWidget(self.progress)
        self.pause = QPushButton("일시정지")
        self.pause.clicked.connect(self.pause_scan)
        self.cancel = QPushButton("취소")
        self.cancel.clicked.connect(self.cancel_scan)
        for button in (self.pause, self.cancel):
            button.setEnabled(False)
            footer.addWidget(button)
        layout.addLayout(footer)
        self.setCentralWidget(container)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.filters_changed)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.major_filter.currentIndexChanged.connect(self.filters_changed)
        self.state_filter.currentIndexChanged.connect(self.filters_changed)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search.setFocus)
        self.update_root_label()
        self.refresh()

    def update_root_label(self):
        self.root_label.setText(self.settings.music_root or "음악 폴더를 등록하면 시작할 수 있습니다")

    def discard_edits(self) -> bool:
        if not self.editor.dirty():
            return True
        if QMessageBox.question(self, "미저장 수정", "미저장 분류 수정을 버리고 이동할까요?") != QMessageBox.StandardButton.Yes:
            return False
        if self.editor.current:
            self.editor.load_track(self.library.track(self.editor.current["id"]))
        return True

    def navigate(self, index):
        if not self.discard_edits():
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self.active_navigation)
            self.navigation.blockSignals(False)
            return
        self.active_navigation = index
        if index in {0, 1}:
            self.pages.setCurrentIndex(0)
            self.offset = 0
            self.refresh()
        elif index == 2:
            self.player.stop()
            DuplicateDialog(self.library, self.settings.duplicate_tolerance_seconds, self).exec()
            self.navigation.setCurrentRow(0)
        elif index == 3:
            self.pages.setCurrentIndex(1)
        elif index == 4:
            self.pages.setCurrentIndex(2)
            self.load_jobs()

    def refresh(self):
        current_id = self.editor.current["id"] if self.editor.current else None
        rows, total = self.library.list_tracks(self.search.text(), self.major_filter.currentData(),
                                                self.state_filter.currentData(), self.navigation.currentRow() == 1,
                                                self.page_size, self.offset, self.sort_column, self.descending)
        self.applied_filters = (self.search.text(), self.major_filter.currentData(), self.state_filter.currentData())
        self.refreshing = True
        self.model.replace(rows)
        self.refreshing = False
        self.count_label.setText(f"검색 결과 {total:,}곡 · {self.offset + 1 if rows else 0}–{self.offset + len(rows)} 표시 · 페이지 {self.offset // self.page_size + 1}")
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(rows) < total)
        if rows:
            index = next((i for i, row in enumerate(rows) if row["id"] == current_id), 0)
            self.table.setCurrentIndex(self.model.index(index, 0))
        else:
            self.editor.current = None
            self.editor.setEnabled(False)
            self.editor.title.setText("곡 없음 · 폴더 스캔 또는 필터를 확인하세요")
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.player.stop()
        self.load_jobs()

    def selection_changed(self, index, previous):
        if self.refreshing or not index.isValid():
            return
        if not self.discard_edits():
            self.table.selectionModel().blockSignals(True)
            self.table.setCurrentIndex(previous)
            self.table.selectionModel().blockSignals(False)
            return
        track = self.model.rows[index.row()]
        self.editor.load_track(track)
        self.player.stop()
        if track["file_state"] == "ready":
            self.player.set_track(track["path"])

    def filters_changed(self, *_):
        if self.discard_edits():
            self.offset = 0
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.refresh()
        else:
            search, major, state = self.applied_filters
            for widget in (self.search, self.major_filter, self.state_filter):
                widget.blockSignals(True)
            self.search.setText(search)
            self.major_filter.setCurrentIndex(self.major_filter.findData(major))
            self.state_filter.setCurrentIndex(self.state_filter.findData(state))
            for widget in (self.search, self.major_filter, self.state_filter):
                widget.blockSignals(False)

    def order_changed(self, column, descending):
        if self.discard_edits():
            self.sort_column, self.descending = column, descending
            self.filters_changed()

    def turn_page(self, direction):
        if self.discard_edits():
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.offset = max(0, self.offset + direction * self.page_size)
            self.refresh()

    def start_scan(self):
        if self.worker and self.worker.isRunning():
            return
        if not self.discard_edits():
            return
        if not self.settings.music_root:
            selected = QFileDialog.getExistingDirectory(self, "음악 루트 선택")
            if not selected:
                return
            try:
                self.library.bind_root(Path(selected))
                self.settings.music_root = selected
                self.settings.save(self.config_path)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "폴더 등록 보류", str(error))
                return
        self.player.stop()
        self.update_root_label()
        self.scan_button.setEnabled(False)
        self.pause.setEnabled(True)
        self.cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText("전체 내용 해시·메타데이터 스캔 중")
        self.worker = ScanWorker(self.library, Path(self.settings.music_root), self.settings.include_subfolders, self)
        self.worker.progress.connect(lambda count, failed: self.status.setText(f"스캔 · {count:,}곡 읽음 · {failed:,}개 확인 실패"))
        self.worker.result.connect(self.scan_result)
        self.worker.error.connect(lambda _: self.status.setText("스캔 실패 · 폴더 접근 상태를 확인하고 작업 이력을 보세요"))
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def pause_scan(self):
        if self.worker and self.worker.isRunning():
            if self.worker.control.paused.is_set():
                self.worker.control.paused.clear()
                self.pause.setText("일시정지")
            else:
                self.worker.control.paused.set()
                self.pause.setText("재개")
                self.status.setText("일시정지 요청 · 현재 파일 읽기 후 대기")

    def cancel_scan(self):
        if self.worker:
            self.worker.control.cancelled.set()
            self.status.setText("취소 요청 · 이미 기록한 관찰은 보존하고 누락 판정을 보류합니다")

    def scan_result(self, result):
        if result.get("cancelled"):
            self.status.setText("스캔 취소 · 누락 판정 없음 · 다시 스캔할 수 있습니다")
        else:
            self.status.setText(f"스캔 완료 · {result['processed']:,}곡 · 신규 {result['new']} · 이동 {result['moved']} · 외부 변경 {result['changed']} · 실패 {result['failed']}")
        # Scanning never discards an editor that the user changed while the worker was running.
        if not self.editor.dirty():
            self.refresh()

    def scan_finished(self):
        self.scan_button.setEnabled(True)
        self.pause.setEnabled(False)
        self.pause.setText("일시정지")
        self.cancel.setEnabled(False)
        self.progress.setVisible(False)
        self.load_jobs()

    def open_settings(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "스캔 중", "스캔을 마치거나 취소한 뒤 설정을 변경하세요.")
            return
        dialog = SettingsDialog(self.settings, self.config_path, self.library, self)
        dialog.settings_saved.connect(self.settings_changed)
        dialog.exec()

    def settings_changed(self, settings):
        self.settings = settings
        apply_theme(QApplication.instance(), settings.theme, settings.font_scale)
        self.update_root_label()

    def load_jobs(self):
        jobs = self.library.jobs()
        self.jobs_table.setRowCount(len(jobs))
        for index, job in enumerate(jobs):
            for column, value in enumerate((job["kind"], STATE_LABELS.get(job["state"], job["state"]),
                                           job["processed"], job["failed"], job["started_at"])):
                item = QTableWidgetItem(str(value))
                item.setToolTip(job["detail"])
                self.jobs_table.setItem(index, column, item)

    def review_external(self, track_id):
        track = self.library.track(track_id)
        observed = track["observed_json"]
        if not observed:
            return
        box = QMessageBox(self)
        box.setWindowTitle("외부 변경 검토")
        box.setText(f"이전: {track['artist']} — {track['title']}\n현재: {observed['artist']} — {observed['title']}\n같은 파일의 수정으로 연결할지 새 파일로 등록할지 선택하세요.")
        inherit = box.addButton("변경으로 연결 · 수동 값 승계", QMessageBox.ButtonRole.AcceptRole)
        fresh = box.addButton("새 파일로 등록 · 이전 이력 보존", QMessageBox.ButtonRole.ActionRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        selected = box.clickedButton()
        if selected not in {inherit, fresh}:
            return
        try:
            self.player.stop()
            self.library.accept_external_change(track_id, inherit_manual=selected == inherit, as_new=selected == fresh)
            self.refresh()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "연결 보류", str(error))

    def closeEvent(self, event):
        if not self.discard_edits():
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self.cancel_scan()
            if not self.worker.wait(3000):
                self.status.setText("스캔 중단을 기다리고 있습니다. 완료 후 창을 닫아 주세요.")
                event.ignore()
                return
        self.player.stop()
        event.accept()
