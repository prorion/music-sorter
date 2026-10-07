import json

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
                              QTableView, QTabWidget, QVBoxLayout, QWidget)

from ..classification import AXES, LABELS, TAXONOMY
from ..catalog import active_catalog
from .review_worker import ReviewWorker

STATES = dict(eligible="저장 가능", blocked="보류", unchanged="변경 없음", applied="저장됨", stale="변경되어 제외", cancelled="취소")


def describe(classification, axes=None):
    return " / ".join(f"{LABELS[axis]}: " + ("미확정" if field["value"] is None else "해당 없음" if field["value"] == []
                         else "·".join(field["value"]) if isinstance(field["value"], list) else field["value"])
                      for axis, field in classification.items() if axes is None or axis in axes) or "값 변경 없음"


class BulkModel(QAbstractTableModel):
    headers = ["곡", "상태", "이유", "변경 전", "변경 후"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        row = self.rows[index.row()]
        original = json.loads(json.loads(row["snapshot"])["classification"])
        proposed = json.loads(row["proposed"]) if row["proposed"] else None
        axes = [axis for axis in AXES if proposed and proposed[axis] != original[axis]] if proposed else None
        if role == Qt.ItemDataRole.ToolTipRole:
            axes = None
        values = [f"{row['title']} / {row['artist']}", STATES[row["state"]], row["reason"],
                  describe(original, axes), describe(proposed, axes) if proposed else "—"]
        return values[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        return self.headers[section] if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole else None

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class BulkDialog(QDialog):
    def __init__(self, library, filters, selected_ids, parent=None, job_id=None):
        super().__init__(parent)
        self.library, self.filters, self.selected_ids = library, dict(filters), list(selected_ids)
        self.worker = None
        self.preview = None
        self.offset = 0
        self.close_requested = False
        self.setWindowTitle("일괄 분류 수정 · 미리보기 후 DB 저장")
        self.resize(1140, 780)
        layout = QVBoxLayout(self)
        note = QLabel("수정한 항목 전체를 수동 보호합니다. 음악 파일은 변경하지 않습니다.\n검색 결과 전체는 현재 페이지 밖의 곡도 포함합니다. 충돌·파일 상태 문제는 미리보기에서 보류합니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.form = QWidget()
        form_layout = QVBoxLayout(self.form)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("대상 범위"))
        self.scope = QComboBox()
        self.scope.addItem(f"현재 페이지에서 선택한 {len(selected_ids)}곡", "selected")
        self.scope.addItem("현재 검색·필터 결과 전체", "filtered")
        self.scope.addItem("라이브러리 전체", "all")
        scope_row.addWidget(self.scope, 1)
        form_layout.addLayout(scope_row)
        names = dict(search="검색어", major="대분류", state="상태", review_only="분류 검토", mood="분위기", concept="컨셉")
        filter_text = " · ".join(f"{names[key]}: {value if key != 'review_only' else '검토 대상'}" for key, value in filters.items() if value)
        form_layout.addWidget(QLabel("현재 필터: " + (filter_text or "필터 없음")))
        self.edits, self.modes, self.values = {}, {}, {}
        grid = QGridLayout()
        for index, axis in enumerate(AXES):
            enabled = QCheckBox(LABELS[axis] + " 수정")
            mode = QComboBox()
            single = axis in {"major", "vocal"}
            for title, data in (("값 지정", "set"), ("미확정", "unknown")) if single else (
                    ("목록 교체", "replace"), ("태그 추가", "add"), ("태그 제거", "remove"), ("미확정", "unknown")):
                mode.addItem(title, data)
            if axis == "concept":
                mode.addItem("검토했으나 해당 태그 없음", "none")
            if single:
                value = QComboBox()
                value.addItem("값 선택", None)
                for name in TAXONOMY[axis]:
                    value.addItem(name, name)
                value.currentIndexChanged.connect(self.invalidate)
            else:
                value = QListWidget()
                value.setMaximumHeight(96)
                catalog = active_catalog()
                options = list(dict.fromkeys(tag for tags in catalog['major'].values() for tag in tags)) if axis == 'subgenre' else catalog[axis]
                for name in options:
                    item = QListWidgetItem(name)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    value.addItem(item)
                value.itemChanged.connect(self.invalidate)
            grid.addWidget(enabled, index, 0)
            grid.addWidget(mode, index, 1)
            grid.addWidget(value, index, 2)
            self.edits[axis], self.modes[axis], self.values[axis] = enabled, mode, value
            enabled.toggled.connect(self.invalidate)
            mode.currentIndexChanged.connect(self.invalidate)
        grid.setColumnStretch(2, 1)
        form_layout.addLayout(grid)
        form_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        self.tabs.addTab(scroll, "수정할 항목")
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        self.model = BulkModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setWordWrap(False)
        for column, width in enumerate((250, 115, 200, 250, 250)):
            self.table.setColumnWidth(column, width)
        preview_layout.addWidget(self.table, 1)
        pages = QHBoxLayout()
        self.previous = QPushButton("이전 페이지")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next = QPushButton("다음 페이지")
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.page_label = QLabel()
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.next)
        preview_layout.addLayout(pages)
        self.tabs.addTab(preview_page, "대상·변경 미리보기")
        self.status = QLabel("항목·방식·값을 선택하고 미리보기를 만드세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.preview_button = QPushButton("미리보기 만들기")
        self.preview_button.clicked.connect(self.start_preview)
        self.apply_button = QPushButton("확인한 변경을 DB에 저장")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.start_apply)
        self.stop_button = QPushButton("작업 취소")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.cancel_work)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        for button in (self.preview_button, self.apply_button, self.stop_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.scope.currentIndexChanged.connect(self.invalidate)
        if job_id:
            self.preview = self.library.bulk_summary(job_id)
            self.form.setEnabled(False)
            self.preview_button.setEnabled(False)
            self.apply_button.setEnabled(self.preview['state'] == 'prepared' and bool(self.preview['eligible']))
            self.tabs.setCurrentIndex(1)
            self.status.setText('저장된 대상·변경 차이입니다. 적용 전 현재 버전을 다시 확인합니다. 중단·완료 작업은 새 미리보기가 필요합니다.')
        self.load_page()

    def invalidate(self, *_):
        self.preview = None
        self.apply_button.setEnabled(False)
        self.model.replace([])
        self.status.setText("수정 조건이 바뀌었습니다. 새 미리보기를 만드세요.")
        self.load_page()

    def operations(self):
        result = {}
        for axis in AXES:
            if not self.edits[axis].isChecked():
                continue
            mode = self.modes[axis].currentData()
            field = self.values[axis]
            value = field.currentData() if isinstance(field, QComboBox) else [field.item(i).text() for i in range(field.count())
                    if field.item(i).checkState() == Qt.CheckState.Checked]
            if mode not in {"unknown", "none"} and (value is None or value == []):
                raise ValueError(f"{LABELS[axis]} 값을 선택하세요.")
            result[axis] = dict(mode=mode, value=value)
        if not result:
            raise ValueError("수정할 항목을 선택하세요.")
        return result

    def start_preview(self):
        try:
            operations = self.operations()
        except ValueError as error:
            QMessageBox.warning(self, "미리보기 보류", str(error))
            return
        scope = self.scope.currentData()
        self.preview = None
        self.begin(lambda cancel, progress: self.library.preview_bulk(operations, scope, self.filters, self.selected_ids, cancel, progress), "preview")

    def start_apply(self):
        if not self.preview:
            return
        count = self.preview["eligible"]
        if QMessageBox.question(self, "일괄 DB 저장", f"미리보기에서 저장 가능한 {count:,}곡의 선택 항목을 수정하고 수동 보호할까요?\n보류 {self.preview['blocked']:,}곡은 건너뜁니다. 음악 파일은 변경하지 않습니다.") != QMessageBox.StandardButton.Yes:
            return
        job_id = self.preview["job_id"]
        self.begin(lambda cancel, progress: self.library.apply_bulk(job_id, cancel, progress), "apply")

    def begin(self, action, mode):
        if self.worker and self.worker.isRunning():
            return
        self.work_mode = mode
        self.form.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.status.setText("대상을 확인하고 있습니다…" if mode == "preview" else "DB 판정을 저장하고 있습니다…")
        self.worker = ReviewWorker(action, self)
        self.worker.progress.connect(lambda count, total: self.status.setText(f"처리 중 · {count:,} / {total:,}곡"))
        self.worker.result.connect(self.completed)
        self.worker.error.connect(self.failed)
        self.worker.finished.connect(self.finished_work)
        self.worker.start()

    def completed(self, result):
        if self.work_mode == "preview":
            if result.get("cancelled"):
                self.status.setText("미리보기를 취소했습니다. DB 분류는 변경하지 않았습니다.")
                return
            self.preview = result
            self.offset = 0
            self.tabs.setCurrentIndex(1)
            self.status.setText(f"대상 {result['total']:,}곡 · 저장 가능 {result['eligible']:,} · 보류 {result['blocked']:,} · 변경 없음 {result['unchanged']:,}")
        else:
            self.status.setText(f"DB 저장 {result['applied']:,}곡 · 미리보기 뒤 변경되어 제외 {result['stale']:,} · 취소 {result['cancelled']:,}")
            self.applied = True
        self.load_page()

    def failed(self, message):
        self.preview = None
        self.status.setText(message)
        QMessageBox.warning(self, "작업 보류", message)

    def finished_work(self):
        self.form.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.apply_button.setEnabled(self.work_mode == "preview" and bool(self.preview and self.preview["eligible"]))
        self.stop_button.setEnabled(False)
        self.load_page()
        if self.close_requested:
            super().reject()

    def load_page(self):
        if not self.preview:
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            self.page_label.setText("미리보기 없음")
            return
        rows = self.library.bulk_rows(self.preview["job_id"], self.offset)
        self.model.replace(rows)
        total = self.preview["total"]
        running = bool(self.worker and self.worker.isRunning())
        self.previous.setEnabled(not running and self.offset > 0)
        self.next.setEnabled(not running and self.offset + len(rows) < total)
        self.page_label.setText(f"대상 {total:,}곡 · {self.offset + 1 if rows else 0}–{self.offset + len(rows)} 표시")

    def turn_page(self, direction):
        self.offset = max(0, self.offset + direction * 200)
        self.load_page()

    def cancel_work(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.status.setText("취소 요청 · 이미 저장한 판정은 유지합니다")

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.close_requested = True
            self.cancel_work()
            return
        super().reject()
