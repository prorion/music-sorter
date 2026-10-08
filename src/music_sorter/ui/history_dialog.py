import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout)

from .bulk import describe
from .wording import ACTIONS
from ..classification import LABELS


class HistoryDialog(QDialog):
    def __init__(self, library, track_id, parent=None):
        super().__init__(parent)
        self.library, self.track_id, self.offset = library, track_id, 0
        self.setWindowTitle('분류 변경 기록')
        self.resize(1080, 650)
        layout = QVBoxLayout(self)
        note = QLabel('선택한 변경 이전의 분류와 자동 수정 허용 상태로 돌아갑니다. 음악 파일은 바꾸지 않습니다.\n이후 변경 기록도 남겨 두고, 이번 되돌리기도 새 기록으로 저장합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['시각 · 변경 종류', '변경 전', '변경 후', '이력 ID'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, width in enumerate((210, 360, 360, 70)):
            self.table.setColumnWidth(i, width)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        for title, action in [('이전 페이지', lambda: self.turn(-1)), ('다음 페이지', lambda: self.turn(1)),
                              ('선택한 변경 이전으로 되돌리기', self.restore), ('닫기', self.reject)]:
            button = QPushButton(title)
            button.setProperty('applyAction', action == self.restore)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.reload()

    def reload(self):
        self.track = self.library.track(self.track_id)
        self.rows, self.total = self.library.classification_history(self.track_id, self.offset)
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            values = [f"{row['created_at']}\n{ACTIONS.get(row['action'].split(':')[0], row['action'])}", describe(json.loads(row['previous'])),
                      describe(json.loads(row['current'])), str(row['id'])]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.status.setText(f'변경 기록 {self.total:,}건 · 현재 저장 번호 {self.track["revision"]} · 페이지 {self.offset // 200 + 1}')

    def turn(self, direction):
        target = self.offset + direction * 200
        if 0 <= target < self.total:
            self.offset = target
            self.reload()

    def restore(self):
        index = self.table.currentRow()
        if index < 0:
            return
        row = self.rows[index]
        previous = json.loads(row['previous'])
        current = self.track['classification']
        def protection(value):
            return ', '.join(LABELS.get(axis, axis) for axis, field in value.items() if field['protected']) or '없음'
        text = ('현재: ' + describe(current) + '\n현재 자동 수정에서 보호하는 항목: ' + protection(current)
                + '\n\n되돌릴 분류: ' + describe(previous) + '\n되돌린 뒤 보호할 항목: ' + protection(previous)
                + '\n\n음악 파일은 바꾸지 않습니다. 이 분류로 되돌릴까요?')
        if QMessageBox.question(self, '분류 되돌리기 확인', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.restore_classification(self.track_id, row['id'], self.track['revision'])
            self.changed = True
            self.offset = 0
            self.reload()
        except ValueError as error:
            QMessageBox.warning(self, '되돌리기 보류', str(error))
