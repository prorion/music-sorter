from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                              QScrollArea, QSplitter, QStackedWidget, QTableView, QTableWidget,
                              QTableWidgetItem, QToolButton, QGridLayout, QVBoxLayout, QWidget)

from ..classification import TAXONOMY
from .. import __version__
from ..database import Library
from ..scanner import ScanControl, scan_library
from ..settings import Settings
from .duplicates import DuplicateDialog
from .bulk import BulkDialog
from .links import LinkDialog
from .editor import TrackEditor
from .player import Player
from .settings_dialog import SettingsDialog
from .theme import apply_theme
from .design import StatCard, TrackDelegate, icon
from .operations import FileDialog
from .playlists import PlaylistDialog
from .wording import readable
from .workflow import WorkflowButton, polish

STATE_LABELS = {"ready": "확인됨", "external_change": "외부 변경", "missing": "누락", "unavailable": "확인 불가",
                "link_pending": "연결 보류", "replaced": "교체된 기록", "unclassified": "미분류", "unresolved": "확인 필요",
                "confirmed": "확정", "running": "진행 중", "completed": "완료", "cancelled": "취소",
                "partial": "부분 완료", "failed": "실패", "interrupted": "이전 실행 중단",
                "preparing": "미리보기 중", "prepared": "변경 내용 확인 대기", "paused": "대기 중",
                "remote": "서버 처리 중", "unknown": "처리 여부 확인 필요"}


class TrackModel(QAbstractTableModel):
    headers = ["No.", "제목", "아티스트", "대분류", "세부 장르", "분류 상태", "파일 상태"]
    order_requested = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.offset = 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self.rows[index.row()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 0:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            return track["path"]
        if role == Qt.ItemDataRole.DisplayRole:
            classification = track["classification"]
            values = [self.offset + index.row() + 1, track["title"], track["artist"], classification["major"]["value"] or "—",
                      " · ".join(classification["subgenre"]["value"] or []),
                      STATE_LABELS[track["review_state"]], STATE_LABELS.get(track["file_state"], track["file_state"])]
            return values[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def replace(self, rows, offset=0):
        self.beginResetModel()
        self.rows = rows
        self.offset = offset
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        mapping = {1: "title_key", 2: "artist_key", 3: "major", 4: "subgenre", 5: "review_state", 6: "file_state"}
        if column in mapping:
            self.order_requested.emit(mapping[column], order == Qt.SortOrder.DescendingOrder)


class ScanWorker(QThread):
    progress = Signal(int, int)
    result = Signal(dict)
    error = Signal(str)

    def __init__(self, library, root, recursive, parent=None, *, exclude=(), resume_job=None):
        super().__init__(parent)
        self.library, self.root, self.recursive = library, root, recursive
        self.exclude, self.resume_job = exclude, resume_job
        self.control = ScanControl()

    def run(self):
        try:
            result = scan_library(self.library, self.root, self.recursive, self.control,
                                  lambda count, failed: self.progress.emit(count, failed), exclude=self.exclude, resume_job=self.resume_job)
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
        self.details_preference = True
        self.applied_filters = ("", "", "", "", "")
        self.setWindowTitle(f"music-sorter · {__version__}")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 700)
        container = QWidget()
        container.setObjectName("workspace")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 14)
        layout.setSpacing(16)
        top = QHBoxLayout()
        self.page_title = QLabel("음악 라이브러리")
        self.page_title.setObjectName("pageTitle")
        top.addWidget(self.page_title)
        self.root_label = QLabel()
        self.root_label.setObjectName("muted")
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.root_label, 1)
        settings_button = QPushButton("⚙ 설정")
        settings_button.setText("설정")
        settings_button.setIcon(icon("settings"))
        settings_button.setAccessibleName("설정 열기")
        settings_button.setToolTip("API 키 · 모델 · 화면 · 음악 라이브러리")
        settings_button.clicked.connect(self.open_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(20)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 20, 14, 18)
        sidebar_layout.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icon("music", "#64B899", 36).pixmap(36, 36))
        sidebar_layout.addWidget(logo)
        brand = QLabel("music-sorter")
        brand.setObjectName("brand")
        sidebar_layout.addWidget(brand)
        tagline = QLabel("나의 음악, 더 선명하게.")
        tagline.setObjectName("subtle")
        sidebar_layout.addWidget(tagline)
        sidebar_layout.addSpacing(12)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        for title, glyph in (("음악 라이브러리", "library"), ("분류 확인", "review"), ("중복 곡 확인", "duplicates"),
                             ("재생목록", "playlist"), ("작업 기록", "history")):
            self.navigation.addItem(QListWidgetItem(icon(glyph), title))
        self.navigation.setIconSize(QSize(20, 20))
        sidebar_layout.addWidget(self.navigation, 1)
        badge = QLabel("내 PC의 음악")
        badge.setObjectName("badge")
        sidebar_layout.addWidget(badge)
        version = QLabel(f"Windows · v{__version__}")
        version.setObjectName("subtle")
        sidebar_layout.addWidget(version)
        body.addWidget(sidebar)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)

        library_page = QWidget()
        main = QVBoxLayout(library_page)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.stat_cards = {}
        for key, label, hint, tint in (("total", "전체 음악", "라이브러리에 등록된 곡", "mint"),
                                       ("confirmed", "분류 완료", "모든 항목을 확인한 곡", "mint"),
                                       ("unclassified", "분류 대기", "아직 분류하지 않은 곡", "neutral"),
                                       ("unresolved", "검토 필요", "직접 확인이 필요한 곡", "neutral")):
            card = StatCard(label, hint, tint)
            self.stat_cards[key] = card
            cards.addWidget(card, 1)
        main.addLayout(cards)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("제목 또는 아티스트 검색")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("제목 또는 아티스트 검색")
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
        tags = QHBoxLayout()
        self.mood_filter = QComboBox()
        self.concept_filter = QComboBox()
        for widget, axis, title in ((self.mood_filter, "mood", "모든 분위기"), (self.concept_filter, "concept", "모든 컨셉")):
            widget.addItem(title, "")
            for tag in TAXONOMY[axis]:
                widget.addItem(tag, tag)
            tags.addWidget(widget)
        tags.addStretch()
        main.addLayout(tags)
        # Task entries stay separate from commands for the selected rows.
        workflow = QGridLayout()
        workflow.setSpacing(8)
        self.scan_button = WorkflowButton(1, '음악 불러오기', '폴더 선택 · 새로 고침')
        self.scan_button.clicked.connect(self.start_scan)
        self.external_button = WorkflowButton(2, '곡 정보 찾기 (선택)', '인터넷 참고 정보 · 분류 전 확인')
        self.external_button.clicked.connect(self.open_external)
        self.classify_button = WorkflowButton(3, 'AI 분류 (유료)', '결과를 앱에 저장 · 파일은 유지')
        self.classify_button.clicked.connect(self.open_classify)
        self.review_button = WorkflowButton(4, '분류 검토', '확인 필요한 곡 · 직접 수정')
        self.review_button.clicked.connect(self.open_review)
        self.file_button = WorkflowButton(5, '파일 정리 (선택)', '미리보기 → 실제 파일 변경')
        self.file_button.clicked.connect(self.open_file_ops)
        self.playlist_button = WorkflowButton(6, '재생목록 만들기', '음악 파일 복사 없이 목록 생성')
        self.playlist_button.clicked.connect(self.open_playlists)
        self.workflow_buttons = (self.scan_button, self.external_button, self.classify_button,
                                 self.review_button, self.file_button, self.playlist_button)
        for i, button in enumerate(self.workflow_buttons):
            workflow.addWidget(button, i // 3, i % 3)
            workflow.setColumnStretch(i % 3, 1)
        main.insertLayout(0, workflow)
        self.workflow_hint = QLabel()
        self.workflow_hint.setObjectName('taskGuide')
        self.workflow_hint.setWordWrap(True)
        main.insertWidget(1, self.workflow_hint)
        actions = QHBoxLayout()
        self.selection_hint = QLabel()
        self.selection_hint.setObjectName('subtle')
        actions.addWidget(self.selection_hint, 1)
        more = self.selection_menu = QToolButton()
        more.setText('선택 곡 메뉴')
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        self.bulk_button = menu.addAction('여러 곡의 분류 직접 수정')
        self.bulk_button.triggered.connect(self.open_bulk)
        details = QAction('곡 상세 표시', menu)
        details.setCheckable(True)
        details.setChecked(True)
        self.details_button = details
        details.triggered.connect(self.toggle_details)
        menu.addAction(details)
        more.setMenu(menu)
        actions.addWidget(more)
        main.addLayout(actions)
        self.splitter = QSplitter()
        self.model = TrackModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setItemDelegate(TrackDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 62)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        for column, width in ((3, 83), (4, 110), (5, 90), (6, 90)):
            self.table.setColumnWidth(column, width)
        self.model.order_requested.connect(self.order_changed)
        self.table.selectionModel().currentRowChanged.connect(self.selection_changed)
        self.table.selectionModel().selectionChanged.connect(self.update_selection_hint)
        self.table.doubleClicked.connect(lambda *_: self.show_details())
        self.splitter.addWidget(self.table)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("detailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setMinimumWidth(300)
        self.editor = TrackEditor(library)
        self.editor.saved.connect(self.refresh)
        self.editor.external_review.connect(self.review_external)
        self.detail_scroll.setWidget(self.editor)
        self.detail_panel = QWidget()
        self.detail_panel.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.addWidget(self.detail_scroll, 1)
        detail_layout.addWidget(self.editor.save_button)
        self.player = Player()
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([780, 320])
        main.addWidget(self.splitter, 1)
        page_controls = QHBoxLayout()
        self.previous = QPushButton("이전 페이지")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next = QPushButton("다음 페이지")
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        page_controls.addWidget(self.previous)
        page_controls.addWidget(self.count_label, 1)
        page_controls.addWidget(self.next)
        main.addLayout(page_controls)
        self.pages.addWidget(library_page)

        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(["작업", "상태", "처리 수", "보류/실패", "시작 시각 (UTC)"])
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.horizontalHeader().setStretchLastSection(True)
        self.pages.addWidget(self.jobs_table)
        self.jobs_table.setShowGrid(False)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.verticalHeader().setDefaultSectionSize(44)
        self.jobs_table.cellDoubleClicked.connect(self.open_job)
        layout.addWidget(self.player)
        footer = QHBoxLayout()
        self.status = QLabel('위의 번호 순서로 진행하세요. 인터넷 조회와 파일 정리는 건너뛰어도 됩니다.')
        self.status.setObjectName("subtle")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(180)
        footer.addWidget(self.progress)
        self.pause = QPushButton("일시정지")
        self.pause.clicked.connect(self.pause_scan)
        self.cancel = QPushButton("불러오기 중단")
        self.cancel.clicked.connect(self.cancel_scan)
        for button in (self.pause, self.cancel):
            button.setEnabled(False)
            button.setVisible(False)
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
        self.mood_filter.currentIndexChanged.connect(self.filters_changed)
        self.concept_filter.currentIndexChanged.connect(self.filters_changed)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search.setFocus)
        self.update_root_label()
        self.refresh()

    def update_root_label(self):
        self.root_label.setText("  /  " + Path(self.settings.music_root).name if self.settings.music_root else "  /  폴더를 등록하면 시작할 수 있습니다")
        self.root_label.setToolTip(self.settings.music_root)

    def toggle_details(self, checked):
        self.details_preference = checked
        self.detail_panel.setVisible(checked and bool(self.model.rows))

    def show_details(self):
        self.details_button.setChecked(True)
        self.toggle_details(True)

    def update_selection_hint(self, *_):
        count = len(self.table.selectionModel().selectedRows())
        self.selection_hint.setText(f'선택 {count:,}곡 · Ctrl/Shift로 여러 곡 선택 · 두 번 클릭하면 상세 보기')

    def update_workflow(self, statistics):
        busy = bool(self.worker and self.worker.isRunning())
        available = bool(statistics['total'] and self.settings.music_root)
        for button in self.workflow_buttons:
            button.setEnabled(not busy and (button is self.scan_button or available))
        self.bulk_button.setEnabled(not busy and bool(statistics['total']))
        self.selection_menu.setVisible(bool(statistics['total']))
        if busy:
            hint, suggested = '음악을 불러오는 중입니다. 완료 후 곡을 선택해 다음 작업을 진행하세요.', None
        elif not statistics['total']:
            hint, suggested = '시작: 1번에서 음악 폴더를 선택하세요. 파일은 읽어서 목록에 등록합니다.', self.scan_button
        elif statistics['unclassified']:
            hint, suggested = ('다음: 곡 선택 → 2번 참고 정보 조회(선택) → 3번 AI 분류. 직접 수정은 선택 곡 메뉴에서 가능합니다.', self.classify_button)
        elif statistics['unresolved']:
            hint, suggested = '다음: 4번에서 확인 필요한 분류를 검토하세요. 5번은 파일 정리가 필요할 때, 6번은 목록을 만들 때 사용합니다.', self.review_button
        else:
            hint, suggested = '분류된 음악을 활용하세요. 5번에서 파일을 정리하거나, 바로 6번에서 재생목록을 만들 수 있습니다.', self.playlist_button
        self.workflow_hint.setText(hint)
        for button in self.workflow_buttons:
            button.setProperty('suggested', button is suggested)
            polish(button)

    def open_review(self):
        if not self.discard_edits():
            return
        # Old search/state filters must not hide songs needing review.
        for widget in (self.search, self.major_filter, self.state_filter, self.mood_filter, self.concept_filter):
            widget.blockSignals(True)
            widget.clear() if widget is self.search else widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self.search_timer.stop()
        if self.navigation.currentRow() == 1:
            self.offset = 0
            self.refresh()
        else:
            self.navigation.setCurrentRow(1)
        self.show_details()

    def open_playlists(self):
        if self.worker and self.worker.isRunning() or not self.settings.music_root or not self.library.statistics()['total']:
            self.status.setText('먼저 1번에서 음악을 불러오고 완료를 기다려 주세요.')
            return
        if not self.discard_edits():
            return
        self.player.stop()
        PlaylistDialog(self.library, self.settings, self).exec()
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'workflow_buttons'):
            compact = event.size().height() < 800
            for button in self.workflow_buttons:
                button.set_compact(compact)
            for card in self.stat_cards.values():
                card.setVisible(not compact)
        if hasattr(self, "detail_panel"):
            visible = self.details_preference and event.size().width() >= 1250 and bool(self.model.rows)
            self.detail_panel.setVisible(visible)
            self.details_button.setChecked(visible)

    def discard_edits(self) -> bool:
        if not self.editor.dirty():
            return True
        if QMessageBox.question(self, "저장하지 않은 수정", "저장하지 않은 분류 수정을 취소하고 이동할까요?") != QMessageBox.StandardButton.Yes:
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
        titles = ("음악 라이브러리", "분류 확인", "중복 곡 확인", "재생목록", "작업 기록")
        self.page_title.setText(titles[index] if 0 <= index < len(titles) else "음악 라이브러리")
        if index in {0, 1}:
            self.pages.setCurrentIndex(0)
            self.offset = 0
            self.refresh()
        elif index == 2:
            if self.worker and self.worker.isRunning():
                self.status.setText('스캔을 마치거나 취소한 뒤 중복 후보를 비교하세요.')
                self.navigation.setCurrentRow(0)
                return
            self.player.stop()
            DuplicateDialog(self.library, self.settings.duplicate_tolerance_seconds, self,
                            root=self.settings.music_root or None).exec()
            self.navigation.setCurrentRow(0)
        elif index == 3:
            self.open_playlists()
            self.navigation.setCurrentRow(0)
        elif index == 4:
            self.pages.setCurrentIndex(1)
            self.load_jobs()

    def refresh(self):
        had_rows = bool(self.model.rows)
        selected_ids = {self.model.rows[index.row()]['id'] for index in self.table.selectionModel().selectedRows()}
        statistics = self.library.statistics()
        for key, card in self.stat_cards.items():
            card.value.setText(f"{statistics[key]:,}")
        current_id = self.editor.current["id"] if self.editor.current else None
        rows, total = self.library.list_tracks(**self.current_filters(), limit=self.page_size, offset=self.offset,
                                             sort=self.sort_column, descending=self.descending)
        self.applied_filters = (self.search.text(), self.major_filter.currentData(), self.state_filter.currentData(),
                                self.mood_filter.currentData(), self.concept_filter.currentData())
        self.refreshing = True
        self.model.replace(rows, self.offset)
        self.refreshing = False
        self.count_label.setText(f"검색 결과 {total:,}곡 · {self.offset + 1 if rows else 0}–{self.offset + len(rows)} 표시 · 페이지 {self.offset // self.page_size + 1}")
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(rows) < total)
        self.previous.setVisible(self.offset > 0)
        self.next.setVisible(self.offset + len(rows) < total)
        if rows:
            index = next((i for i, row in enumerate(rows) if row["id"] == current_id), 0)
            self.table.setCurrentIndex(self.model.index(index, 0))
            matching = [i for i, row in enumerate(rows) if row['id'] in selected_ids]
            if matching:
                selection = self.table.selectionModel()
                selection.clearSelection()
                for i in matching:
                    selection.select(self.model.index(i, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            if not had_rows:
                visible = self.details_preference and self.width() >= 1250
                self.detail_panel.setVisible(visible)
                self.details_button.setChecked(visible)
        else:
            self.editor.current = None
            self.editor.setEnabled(False)
            self.editor.save_button.setEnabled(False)
            self.detail_panel.setVisible(False)
            self.details_button.setChecked(False)
            self.editor.title.setText('표시할 곡 없음 · 음악 불러오기 또는 검색 조건을 확인하세요')
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.player.stop()
        self.load_jobs()
        self.details_button.setEnabled(bool(rows))
        self.update_selection_hint()
        self.update_workflow(statistics)

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
            self.player.set_track(track["path"], title=track["title"], artist=track["artist"])

    def filters_changed(self, *_):
        if self.discard_edits():
            self.offset = 0
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.refresh()
        else:
            search, major, state, mood, concept = self.applied_filters
            widgets = (self.search, self.major_filter, self.state_filter, self.mood_filter, self.concept_filter)
            for widget in widgets:
                widget.blockSignals(True)
            self.search.setText(search)
            self.major_filter.setCurrentIndex(self.major_filter.findData(major))
            self.state_filter.setCurrentIndex(self.state_filter.findData(state))
            self.mood_filter.setCurrentIndex(self.mood_filter.findData(mood))
            self.concept_filter.setCurrentIndex(self.concept_filter.findData(concept))
            for widget in widgets:
                widget.blockSignals(False)

    def order_changed(self, column, descending):
        if self.discard_edits():
            self.sort_column, self.descending = column, descending
            self.filters_changed()

    def current_filters(self):
        return dict(search=self.search.text(), major=self.major_filter.currentData(), state=self.state_filter.currentData(),
                    review_only=self.navigation.currentRow() == 1, mood=self.mood_filter.currentData(), concept=self.concept_filter.currentData())

    def open_bulk(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "스캔 중", "스캔을 마치거나 취소한 뒤 일괄 수정하세요.")
            return
        if not self.discard_edits():
            return
        selected = [self.model.rows[index.row()]["id"] for index in self.table.selectionModel().selectedRows()]
        self.player.stop()
        BulkDialog(self.library, self.current_filters(), selected, self).exec()
        self.refresh()

    def open_file_ops(self, job_id=None):
        if self.worker and self.worker.isRunning() or not self.settings.music_root:
            self.status.setText('폴더를 등록하고 진행 중인 스캔을 마친 뒤 파일 작업을 실행하세요.')
            return
        if not self.discard_edits():
            return
        selected = [self.model.rows[index.row()]['id'] for index in self.table.selectionModel().selectedRows()]
        self.player.stop()
        FileDialog(self.library, self.settings, selected, self.current_filters(), self,
                   job_id=job_id if isinstance(job_id, str) else None).exec()
        self.refresh()

    def open_classify(self, job_id=None):
        if self.worker and self.worker.isRunning() or not self.settings.music_root:
            self.status.setText('음악 폴더를 등록하고 스캔을 마친 뒤 분류 계획을 준비하세요.')
            return
        if not self.discard_edits():
            return
        from .classify_dialog import ClassifyDialog
        selected = [self.model.rows[index.row()]['id'] for index in self.table.selectionModel().selectedRows()]
        ClassifyDialog(self.library, self.settings, selected, self.current_filters(), self,
                       job_id=job_id if isinstance(job_id, str) else None).exec()
        self.refresh()

    def open_job(self, row, column):
        job_id = self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        job = next((job for job in self.library.jobs() if job['id'] == job_id), None)
        if job is None:
            return
        if job['kind'] == 'file_preview':
            self.open_file_ops(job['id'])
        elif job['kind'] == 'llm':
            self.open_classify(job['id'])
        elif job['kind'] == 'manual_bulk':
            if not self.discard_edits():
                return
            BulkDialog(self.library, {}, [], self, job_id=job['id']).exec()
            self.refresh()
        elif job['kind'] == 'scan' and job['state'] in {'cancelled', 'failed', 'interrupted'}:
            if QMessageBox.question(self, '스캔 체크포인트 재개', '저장된 관찰과 같은 범위로 스캔을 재개할까요?\n모든 파일 내용은 다시 검증하며, 일치하는 완료 파일은 메타데이터를 재사용합니다. 음악 파일은 바꾸지 않습니다.',
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.start_scan(job['id'])
        else:
            QMessageBox.information(self, '작업 상세', job['detail'] or '상세 기록 없음')

    def open_external(self):
        if self.worker and self.worker.isRunning() or not self.settings.music_root:
            self.status.setText('음악 폴더를 등록하고 스캔을 마친 뒤 정보를 보완하세요.')
            return
        if not self.discard_edits():
            return
        from .external_dialog import ExternalDialog
        selected = [self.model.rows[index.row()]['id'] for index in self.table.selectionModel().selectedRows()]
        ExternalDialog(self.library, self.settings, selected, self.current_filters(), self).exec()
        self.refresh()

    def turn_page(self, direction):
        if self.discard_edits():
            for edit in self.editor.edits.values():
                edit.setChecked(False)
            self.offset = max(0, self.offset + direction * self.page_size)
            self.refresh()

    def start_scan(self, resume_job=None):
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
        self.status.setText('음악 파일과 곡 정보를 읽는 중입니다. 파일 내용은 바꾸지 않습니다.')
        self.worker = ScanWorker(self.library, Path(self.settings.music_root), self.settings.include_subfolders, self,
                                 exclude=self.settings.scan_exclude_folders, resume_job=resume_job if isinstance(resume_job, str) else None)
        self.worker.progress.connect(lambda count, failed: self.status.setText(f"스캔 · {count:,}곡 읽음 · {failed:,}개 확인 실패"))
        self.worker.result.connect(self.scan_result)
        self.worker.error.connect(lambda _: self.status.setText("스캔 실패 · 폴더 접근 상태를 확인하고 작업 기록을 보세요"))
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()
        for button in (self.pause, self.cancel):
            button.setVisible(True)
        self.update_workflow(self.library.statistics())

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
            self.status.setText("취소 요청 · 읽은 곡 정보는 보존합니다. 이번 스캔에서는 사라진 파일을 판단하지 않습니다")

    def scan_result(self, result):
        if self.settings.notify_on_completion:
            QApplication.alert(self, 3000)
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
        self.pause.setVisible(False)
        self.cancel.setVisible(False)
        self.update_workflow(self.library.statistics())
        self.load_jobs()

    def open_settings(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "스캔 중", "스캔을 마치거나 취소한 뒤 설정을 변경하세요.")
            return
        if not self.discard_edits():
            return
        dialog = SettingsDialog(self.settings, self.config_path, self.library, self)
        dialog.settings_saved.connect(self.settings_changed)
        dialog.exec()

    def settings_changed(self, settings):
        self.settings = settings
        apply_theme(QApplication.instance(), settings.theme, settings.font_scale)
        self.update_root_label()
        self.refresh()

    def load_jobs(self):
        jobs = self.library.jobs()
        self.jobs_table.setRowCount(len(jobs))
        for index, job in enumerate(jobs):
            for column, value in enumerate(({"scan": "음악 폴더 스캔", "manual_bulk": "여러 곡 분류 수정", 'llm': 'AI 음악 분류', 'external': '인터넷 곡 정보 조회', 'file_preview': '파일 정리·복구', 'playlists': '재생목록 만들기', 'duplicate_delete': '중복 파일 휴지통 이동'}.get(job["kind"], job["kind"]), STATE_LABELS.get(job["state"], job["state"]),
                                           job["processed"], job["failed"], job["started_at"])):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, job['id'])
                item.setToolTip(readable(job["detail"]))
                self.jobs_table.setItem(index, column, item)

    def review_external(self, track_id):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "스캔 중", "스캔을 마치거나 취소한 뒤 파일 연결을 검토하세요.")
            return
        track = self.library.track(track_id)
        if track["file_state"] == "link_pending":
            self.player.stop()
            dialog = LinkDialog(self.library, track_id, self)
            dialog.exec()
            if hasattr(dialog, "result_id"):
                self.editor.current = self.library.track(dialog.result_id)
            self.refresh()
            return
        observed = track["observed_json"]
        if not observed:
            return
        box = QMessageBox(self)
        box.setWindowTitle("앱 밖에서 바뀐 파일 확인")
        box.setText(f"이전: {track['artist']} — {track['title']}\n현재: {observed['artist']} — {observed['title']}\n같은 파일의 수정으로 연결할지 새 파일로 등록할지 선택하세요.")
        inherit = box.addButton("같은 곡으로 유지 · 직접 수정한 분류 유지", QMessageBox.ButtonRole.AcceptRole)
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
