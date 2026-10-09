import json
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..classification import TAXONOMY
from ..playlists import PlaylistExporter
from .operations import OperationWorker
from .workflow import task_guide


class PlaylistDialog(QDialog):
    def __init__(self, library, settings, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.worker = library, settings, None
        self.offset = 0
        self.setWindowTitle('재생목록 생성')
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        heading = QLabel('재생목록')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel(f'장르·분위기·컨셉·CCM 및 검토 목록을 음악 폴더에 만듭니다.\n.{settings.playlist_format} 형식으로 저장합니다. 아직 분류를 확인하지 못했거나 중복 여부가 불분명한 곡은 확인용 목록으로 모읍니다.\n사용자가 만든 목록이나 수정한 목록은 보존하고 새 출력 이름을 사용합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        task_guide(layout, '전체 라이브러리의 분류별·검토 목록을 생성합니다. 기존 조합 목록도 갱신하며 음악 파일은 바꾸지 않습니다.')
        self.custom_toggle = QCheckBox('조건을 조합한 목록 추가 (선택)')
        layout.addWidget(self.custom_toggle)
        self.custom_form = QWidget()
        custom_layout = QVBoxLayout(self.custom_form)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText('추가할 목록 이름 · 예: 새벽에 듣는 가요')
        custom_layout.addWidget(self.name)
        filters = QHBoxLayout()
        self.filters = {}
        for axis, title in (('major', '대분류'), ('vocal', '보컬/연주'), ('mood', '분위기'), ('concept', '컨셉')):
            combo = QComboBox()
            combo.addItem(title + ' 선택 안 함', '')
            for item in TAXONOMY[axis]:
                combo.addItem(item, item)
            filters.addWidget(combo)
            self.filters[axis] = combo
        custom_layout.addLayout(filters)
        custom_layout.addWidget(QLabel('선택한 조건을 모두 만족하는 곡을 모읍니다. 이름과 조건을 함께 입력하세요.'))
        self.custom_form.setVisible(False)
        self.custom_toggle.toggled.connect(self.custom_form.setVisible)
        layout.addWidget(self.custom_form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['출력 이름', '상태', '갱신 시각'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 460)
        layout.addWidget(self.table, 1)
        pages = QHBoxLayout()
        self.previous, self.next, self.page_label = QPushButton('이전'), QPushButton('다음'), QLabel()
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next.clicked.connect(lambda: self.turn_page(1))
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label)
        pages.addStretch()
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.status = QLabel('등록된 곡을 바탕으로 목록을 생성하세요.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.generate = QPushButton('생성 시작')
        self.generate.setProperty('primary', True)
        self.generate.clicked.connect(self.start)
        self.cancel_button = QPushButton('작업 취소')
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for button in (self.generate, self.cancel_button, close):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        with self.library.connection() as db:
            total = db.execute('SELECT count(*) FROM playlist_outputs').fetchone()[0]
            self.offset = min(self.offset, max(0, (total - 1) // 200) * 200)
            rows = db.execute('SELECT * FROM playlist_outputs ORDER BY path LIMIT 200 OFFSET ?', (self.offset,)).fetchall()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate((Path(row['path']).name, '갱신 필요' if row['dirty'] else '생성됨', row['updated_at'])):
                self.table.setItem(i, j, QTableWidgetItem(value))
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(rows) < total)
        self.previous.setVisible(self.offset > 0)
        self.next.setVisible(self.offset + len(rows) < total)
        self.page_label.setText(f'{self.offset + 1 if rows else 0}–{self.offset + len(rows)} / {total:,}개')

    def turn_page(self, delta):
        self.offset = max(0, self.offset + delta * 200)
        self.refresh()

    def start(self):
        if self.worker:
            return
        custom = []
        if self.custom_toggle.isChecked():
            if not self.name.text().strip():
                self.status.setText('추가할 목록 이름을 입력하세요. 기본 목록만 만들려면 「조건을 조합한 목록 추가」를 끄세요.')
                self.name.setFocus()
                return
            filters = {axis: combo.currentData() for axis, combo in self.filters.items() if combo.currentData()}
            if not filters:
                self.status.setText('조합 목록에 사용할 조건을 선택하세요.')
                return
            # Preserve other user-created combination definitions when adding/updating one.
            with self.library.connection() as db:
                for row in db.execute('SELECT definition FROM playlist_outputs ORDER BY updated_at,path'):
                    item = json.loads(row[0])
                    if item['name'].startswith('[조합] '):
                        custom.append({'name': item['name'][5:], 'filters': item['filters']})
            custom = [item for item in custom if item['name'] != self.name.text().strip()]
            custom.append({'name': self.name.text().strip(), 'filters': filters})
        else:
            with self.library.connection() as db:
                custom = [{'name': item['name'][5:], 'filters': item['filters']} for row in db.execute('SELECT definition FROM playlist_outputs ORDER BY updated_at,path')
                          if (item := json.loads(row[0]))['name'].startswith('[조합] ')]
        custom = list({item['name']: item for item in custom}.values())
        exporter = PlaylistExporter(self.library, Path(self.settings.music_root), self.settings.duplicate_tolerance_seconds,
                                    file_format=self.settings.playlist_format)
        self.worker = OperationWorker(lambda control, progress: exporter.export(custom, control, progress), self)
        self.worker.result.connect(lambda result: self.status.setText(f'생성 {result["completed"]} · 보류 {result["blocked"]} · 제외 곡 {result.get("skipped", 0)}'))
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self.worker_finished)
        self.generate.setEnabled(False)
        self.custom_toggle.setEnabled(False)
        self.custom_form.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.worker.start()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.generate.setEnabled(True)
        self.custom_toggle.setEnabled(True)
        self.custom_form.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
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
        layout.addWidget(QLabel('같은 음악을 장르·분위기·컨셉별로 모아 들을 수 있어요.\n음악 파일을 복사하지 않고 재생할 곡 목록만 만듭니다.'))
        open_button = QPushButton('재생목록 설정 열기')
        open_button.setProperty('primary', True)
        open_button.clicked.connect(self.open)
        layout.addWidget(open_button)
        layout.addStretch()

    def open(self):
        settings = self.settings_getter()
        if not settings.music_root:
            self.window().status.setText('먼저 음악 라이브러리의 1번에서 음악을 불러오세요.')
            return
        worker = getattr(self.window(), 'worker', None)
        if worker and worker.isRunning():
            self.window().status.setText('진행 중인 스캔을 마치거나 취소한 뒤 재생목록을 생성하세요.')
            return
        if hasattr(self.window(), 'player'):
            self.window().player.stop()
        PlaylistDialog(self.library, settings, self).exec()
