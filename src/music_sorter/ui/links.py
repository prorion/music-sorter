from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .review_worker import ReviewWorker
from ..classification import LABELS


class LinkDialog(QDialog):
    def __init__(self, library, track_id, parent=None):
        super().__init__(parent)
        self.library = library
        self.track = library.track(track_id)
        self.worker = None
        self.offset = 0
        self.close_requested = False
        self.setWindowTitle("연결 보류 검토 · 파일 변경 없음")
        self.resize(1050, 740)
        layout = QVBoxLayout(self)
        title = QLabel(f"새 경로: {self.track['path']}\n현재 SHA-256: {self.track['hash']}\n기존 기록을 선택하거나 별도 신규 파일로 확정하세요.")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["기존 제목 / 아티스트", "기존 경로", "기록 상태"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 460)
        self.table.currentCellChanged.connect(self.selection_changed)
        layout.addWidget(self.table, 1)
        pages = QHBoxLayout()
        self.previous = QPushButton("이전 후보")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next = QPushButton("다음 후보")
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.count = QLabel()
        pages.addWidget(self.previous)
        pages.addWidget(self.count, 1)
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.comparison = QPlainTextEdit()
        self.comparison.setReadOnly(True)
        self.comparison.setMaximumHeight(180)
        layout.addWidget(self.comparison)
        actions = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("처리 선택", None)
        self.mode.addItem("선택한 기존 기록에 연결", "link")
        self.mode.addItem("기존 기록을 승계하지 않고 신규 파일로 확정", "new")
        self.inherit = QComboBox()
        self.inherit.addItem("분류 처리 선택", None)
        self.inherit.addItem("기존 분류·수동 보호 유지", True)
        self.inherit.addItem("분류 초기화 · 이전 값은 이력 보존", False)
        self.mode.currentIndexChanged.connect(lambda: self.inherit.setEnabled(self.mode.currentData() == "link"))
        self.inherit.setEnabled(False)
        actions.addWidget(self.mode)
        actions.addWidget(self.inherit)
        layout.addLayout(actions)
        self.status = QLabel("연결 저장 시 새 파일 내용을 재확인하고 기존 경로의 부재를 확인합니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("선택한 연결을 DB에 저장")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self.save)
        self.close_button = QPushButton("보류 / 닫기")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.reload()

    def reload(self):
        self.candidates, total = self.library.link_candidates(self.track["id"], self.offset)
        self.total = total
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(self.candidates))
        for index, row in enumerate(self.candidates):
            values = (f"{row['title']} / {row['artist']}", row["path"], "누락" if row["file_state"] == "missing" else "확인 불가")
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.count.setText(f"동일 해시 후보 {total:,}개 · {self.offset + 1 if self.candidates else 0}–{self.offset + len(self.candidates)} 표시")
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(self.candidates) < total)
        self.mode.model().item(1).setEnabled(total > 0)
        self.selection_changed(-1)

    def turn_page(self, direction):
        self.offset = max(0, self.offset + direction * 200)
        self.reload()

    def selection_changed(self, row, *_):
        if not 0 <= row < len(getattr(self, "candidates", [])):
            self.comparison.setPlainText("승계할 후보를 명시적으로 선택하세요. 기본 선택은 없습니다." if self.total else
                                        "현재 승계 가능한 기존 기록이 없습니다. 신규 파일로 확정하거나 재스캔으로 기존 후보를 확인하세요.")
            return
        candidate = self.candidates[row]
        classification = []
        for axis, field in candidate["classification"].items():
            value = field["value"]
            label = "미확정" if value is None else "해당 없음" if value == [] else " · ".join(value) if isinstance(value, list) else value
            classification.append(f"{LABELS[axis]}: {label}" + (" · 수동 보호" if field["protected"] else ""))
        self.comparison.setPlainText(f"기존 SHA-256: {candidate['hash']}\n기존 분류:\n" + "\n".join(classification))

    def save(self):
        if self.worker and self.worker.isRunning():
            return
        mode = self.mode.currentData()
        index = self.table.currentRow()
        if mode is None or (mode == "link" and (not 0 <= index < len(self.candidates) or self.inherit.currentData() is None)):
            QMessageBox.warning(self, "선택 필요", "처리 방식·기존 후보·분류 처리 방식을 선택하세요.")
            return
        old = self.candidates[index] if mode == "link" else None
        inherit = self.inherit.currentData()
        self.worker = ReviewWorker(lambda cancel, _: self.library.resolve_link(self.track["id"], self.track["revision"],
                                old["id"] if old else None, old["revision"] if old else None, inherit, cancel), self)
        self.worker.result.connect(self.saved)
        self.worker.error.connect(lambda message: QMessageBox.warning(self, "연결 보류", message))
        self.worker.finished.connect(self.finished_work)
        for widget in (self.table, self.mode, self.inherit, self.previous, self.next, self.save_button):
            widget.setEnabled(False)
        self.status.setText("내용·경로 확인 중 · 창을 닫으면 가능한 안전 지점에서 취소합니다")
        self.worker.start()

    def saved(self, result):
        if isinstance(result, str):
            self.result_id = result

    def finished_work(self):
        if hasattr(self, "result_id"):
            self.accept()
            return
        if self.close_requested:
            super().reject()
            return
        for widget in (self.table, self.mode, self.save_button):
            widget.setEnabled(True)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(self.candidates) < self.total)
        self.inherit.setEnabled(self.mode.currentData() == "link")
        if not hasattr(self, "result_id"):
            self.status.setText("연결을 저장하지 않았습니다. 후보·파일 상태를 확인하세요.")

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.close_requested = True
            self.worker.cancel.set()
            self.status.setText("확인 중단을 기다립니다. 완료된 DB 연결은 유지합니다.")
            return
        super().reject()
