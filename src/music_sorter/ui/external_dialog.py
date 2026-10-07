import json

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                              QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)

from ..database import now
from ..external import ExternalLookup
from ..settings import CredentialStore
from .operations import OperationWorker


class ExternalDialog(QDialog):
    def __init__(self, library, settings, selected, filters, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.selected, self.filters = library, settings, selected, filters
        self.worker = None
        self.setWindowTitle('외부 음악 정보 · 조회와 식별')
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        heading = QLabel('외부 음악 정보')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel('MusicBrainz 녹음 식별과 Last.fm 참고 태그를 캐시합니다.\n모호한 후보의 정보를 LLM 근거로 자동 전달하지 않습니다. 조회·갱신만으로 곡의 분류를 변경하지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QHBoxLayout()
        self.scope = QComboBox()
        for title, value in ((f'선택한 {len(selected)}곡', 'selected'), ('검색·필터 전체', 'filtered'), ('라이브러리 전체', 'all')):
            self.scope.addItem(title, value)
        form.addWidget(self.scope)
        self.force = QCheckBox('기존 캐시도 새로 조회')
        form.addWidget(self.force)
        form.addStretch()
        layout.addLayout(form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['곡', '출처', '식별·조회 상태'])
        self.table.setColumnWidth(0, 330)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.status = QLabel('설정의 연락처·키가 없으면 해당 출처는 건너뜁니다. 전송 정보: 제목·아티스트 검색어.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('선택 범위 정보 조회')
        self.start_button.setProperty('primary', True)
        self.start_button.clicked.connect(self.start)
        self.stop = QPushButton('조회 중단')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for widget in (self.start_button, self.stop, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)

    def start(self):
        if self.worker:
            return
        if self.scope.currentData() == 'selected':
            ids = list(self.selected)
        else:
            clause, params = self.library._filter_clause(**(self.filters if self.scope.currentData() == 'filtered' else {}))
            with self.library.connection() as db:
                ids = [r[0] for r in db.execute('SELECT id FROM tracks' + clause + ' ORDER BY id', params)]
        if not ids:
            self.status.setText('대상 곡이 없습니다.')
            return
        force = self.force.isChecked()
        def action(control, progress):
            lookup = ExternalLookup(self.library, self.settings, CredentialStore().get('lastfm') or '')
            job = self.library.start_job('external')
            count = failed = 0
            samples = []
            try:
                for track_id in ids:
                    control.checkpoint()
                    track = self.library.track(track_id)
                    if track['file_state'] != 'ready':
                        failed += 1
                        continue
                    for service in ('musicbrainz', 'lastfm'):
                        control.checkpoint()
                        try:
                            result = lookup.lookup(track, service, force)
                        except ValueError as error:
                            result = dict(state='failed', reason=str(error))
                            failed += 1
                        if len(samples) < 200:
                            samples.append((track['title'], service, result.get('reason', result['state'])))
                    count += 1
                    progress(count, failed)
                self.library.job_state(job, 'partial' if failed else 'completed', f'외부 정보 {count}곡 · 오류 {failed} · 분류 변경 없음')
            except InterruptedError:
                self.library.job_state(job, 'cancelled', f'조회 중단. 완료 캐시 {count}곡 보존')
            return samples, count, failed
        self.worker = OperationWorker(action, self)
        for widget in (self.start_button, self.scope, self.force):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker.result.connect(self.result)
        self.worker.error.connect(self.status.setText)
        self.worker.progress.connect(lambda count, failed: self.status.setText(f'조회 {count:,}곡 · 오류 {failed:,}'))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def result(self, result):
        rows, count, failed = result
        self.table.setRowCount(len(rows))
        states = dict(matched='녹음 연결', reference='참고 태그', ambiguous='연결 보류', not_found='결과 없음')
        for i, values in enumerate(rows):
            for j, value in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(states.get(value, value)))
        self.status.setText(f'조회 {count:,}곡 · 오류 {failed:,} · 목록에는 첫 200개 조회 기록을 표시합니다. 전체 캐시는 DB에 보존합니다.')

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        for widget in (self.start_button, self.scope, self.force):
            widget.setEnabled(True)
        self.stop.setEnabled(False)

    def reject(self):
        if not self.worker:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            super().closeEvent(event)
