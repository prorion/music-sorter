import json
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..classification import TAXONOMY
from ..playlists import PlaylistExporter
from .operations import OperationWorker


class PlaylistDialog(QDialog):
    def __init__(self, library, settings, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.worker = library, settings, None
        self.setWindowTitle('재생목록 생성')
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        heading = QLabel('재생목록')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel('장르·분위기·컨셉·CCM 및 검토 목록을 음악 폴더에 만듭니다.\nUTF-8 · CRLF · 상대 경로. 미확정·미해결 중복은 일반 목록에서 제외합니다.\n사용자가 만든 목록이나 수정한 목록은 보존하고 새 출력 이름을 사용합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.name = QLineEdit()
        self.name.setPlaceholderText('선택: 조합 재생목록 이름')
        layout.addWidget(self.name)
        filters = QHBoxLayout()
        self.filters = {}
        for axis, title in (('major', '대분류'), ('vocal', '보컬/연주'), ('mood', '분위기'), ('concept', '컨셉')):
            combo = QComboBox()
            combo.addItem(title + ' 선택 안 함', '')
            for item in TAXONOMY[axis]:
                combo.addItem(item, item)
            filters.addWidget(combo)
            self.filters[axis] = combo
        layout.addLayout(filters)
        layout.addWidget(QLabel('선택한 조건을 모두 만족하는 곡으로 조합 목록을 추가합니다.'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['출력 이름', '상태', '갱신 시각'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 460)
        layout.addWidget(self.table, 1)
        self.status = QLabel('등록된 곡을 바탕으로 목록을 생성하세요.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.generate = QPushButton('기본 목록과 선택한 조합 생성')
        self.generate.setProperty('primary', True)
        self.generate.clicked.connect(self.start)
        self.cancel_button = QPushButton('작업 취소')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for button in (self.generate, self.cancel_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        with self.library.connection() as db:
            rows = db.execute('SELECT * FROM playlist_outputs ORDER BY path LIMIT 200').fetchall()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate((Path(row['path']).name, '갱신 필요' if row['dirty'] else '생성됨', row['updated_at'])):
                self.table.setItem(i, j, QTableWidgetItem(value))

    def start(self):
        if self.worker:
            return
        custom = []
        if self.name.text().strip():
            filters = {axis: combo.currentData() for axis, combo in self.filters.items() if combo.currentData()}
            if not filters:
                self.status.setText('조합 목록에 사용할 조건을 선택하세요.')
                return
            # Preserve other user-created combination definitions when adding/updating one.
            with self.library.connection() as db:
                for row in db.execute('SELECT definition FROM playlist_outputs'):
                    item = json.loads(row[0])
                    if item['name'].startswith('[조합] '):
                        custom.append({'name': item['name'][5:], 'filters': item['filters']})
            custom = [item for item in custom if item['name'] != self.name.text().strip()]
            custom.append({'name': self.name.text().strip(), 'filters': filters})
        else:
            with self.library.connection() as db:
                custom = [{'name': item['name'][5:], 'filters': item['filters']} for row in db.execute('SELECT definition FROM playlist_outputs')
                          if (item := json.loads(row[0]))['name'].startswith('[조합] ')]
        exporter = PlaylistExporter(self.library, Path(self.settings.music_root), self.settings.duplicate_tolerance_seconds)
        self.worker = OperationWorker(lambda control, progress: exporter.export(custom, control, progress), self)
        self.worker.result.connect(lambda result: self.status.setText(f'생성 {result["completed"]} · 보류 {result["blocked"]} · 제외 곡 {result.get("skipped", 0)}'))
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self.worker_finished)
        self.generate.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker.start()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.generate.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.refresh()

    def reject(self):
        if self.worker:
            self.worker.control.cancelled.set()
            self.status.setText('취소 요청 · 완료 목록은 유지합니다.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class PlaylistPage(QWidget):
    def __init__(self, library, settings_getter, parent=None):
        super().__init__(parent)
        self.library, self.settings_getter = library, settings_getter
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('같은 음악을 장르·분위기·컨셉별로 모아 들을 수 있어요.\n곡 파일을 복제하지 않고 상대 경로 목록을 만듭니다.'))
        open_button = QPushButton('재생목록 관리·생성')
        open_button.setProperty('primary', True)
        open_button.clicked.connect(self.open)
        layout.addWidget(open_button)
        layout.addStretch()

    def open(self):
        settings = self.settings_getter()
        if not settings.music_root:
            return
        if hasattr(self.window(), 'player'):
            self.window().player.stop()
        PlaylistDialog(self.library, settings, self).exec()
