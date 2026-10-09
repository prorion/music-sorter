import json

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)

from ..database import now
from ..external import ExternalLookup
from ..settings import CredentialStore
from .operations import OperationWorker
from .wording import PROVIDERS, readable
from .workflow import task_guide


class ExternalDialog(QDialog):
    def __init__(self, library, settings, selected, filters, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.selected, self.filters = library, settings, list(selected), filters
        self.worker = None
        self.offset = 0
        self.setWindowTitle('인터넷에서 곡 정보 찾기')
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        heading = QLabel('인터넷 곡 정보')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel('멜론·벅스의 공개 곡 정보를 검색하고 출처별 장르를 대조합니다. MusicBrainz·Last.fm 정보도 함께 찾습니다.\n검색·대조에는 AI 비용이 들지 않습니다. 이 조회만으로 기존 분류나 음악 파일이 바뀌지는 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        task_guide(layout, '대상 선택 → 조회 시작 → 결과를 두 번 클릭해 출처 확인. 이후 메인 3번에서 AI 분류를 진행하세요.')
        form = QHBoxLayout()
        self.scope = QComboBox()
        for title, value in ((f'선택한 {len(selected)}곡', 'selected'), ('검색·필터 전체', 'filtered'), ('라이브러리 전체', 'all')):
            self.scope.addItem(title, value)
        form.addWidget(self.scope)
        self.force = QCheckBox('이전에 찾은 정보도 다시 검색')
        form.addWidget(self.force)
        form.addStretch()
        layout.addLayout(form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['곡', '출처', '조회 결과'])
        self.table.setColumnWidth(0, 330)
        self.table.setColumnWidth(1, 160)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(self.inspect)
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
        self.status = QLabel('국내 검색은 별도 키 없이 사용할 수 있습니다. 전송 정보: 제목·아티스트 검색어. MusicBrainz·Last.fm은 설정이 필요합니다.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('조회 시작')
        self.start_button.setProperty('primary', True)
        self.start_button.clicked.connect(self.start)
        self.stop = QPushButton('조회 중단')
        self.stop.setEnabled(False)
        self.stop.setVisible(False)
        self.stop.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = self.close_button = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for widget in (self.start_button, self.stop, close):
            widget.setAutoDefault(False)
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.refresh()

    def scope_changed(self):
        self.offset = 0
        self.refresh()

    def refresh(self):
        if self.scope.currentData() == 'selected':
            total = len(self.selected)
            tracks = [self.library.track(identity) for identity in self.selected[self.offset:self.offset + 100]]
        else:
            tracks, total = self.library.list_tracks(limit=100, offset=self.offset, **(self.filters if self.scope.currentData() == 'filtered' else {}))
        lookup = ExternalLookup(self.library, self.settings)
        services = ('domestic', 'musicbrainz', 'lastfm') if self.settings.domestic_enabled else ('musicbrainz', 'lastfm')
        self.rows = [(track, service, lookup.cached(track, service)) for track in tracks for service in services]
        self.table.setRowCount(len(self.rows))
        states = dict(matched='녹음 연결', reference='참고 태그', ambiguous='연결 보류', not_found='결과 없음', skipped='건너뜀', failed='조회 오류')
        states.update(corroborated='여러 출처에서 일치', conflict='출처마다 정보가 다름', insufficient='근거 부족')
        for i, (track, service, result) in enumerate(self.rows):
            state = states.get(result['state'], result['state']) if result else '미조회'
            for j, value in enumerate((track['title'] + ' · ' + track['artist'], PROVIDERS.get(service, service), state)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(i, j, item)
        self.previous.setEnabled(self.offset > 0 and not self.worker)
        self.next.setEnabled(self.offset + len(tracks) < total and not self.worker)
        self.previous.setVisible(self.offset > 0)
        self.next.setVisible(self.offset + len(tracks) < total)
        self.page_label.setText(f'{self.offset + 1 if tracks else 0}–{self.offset + len(tracks)} / {total:,}곡 · 두 번 클릭: 찾은 곡 비교·선택')

    def turn_page(self, delta):
        if not self.worker:
            self.offset = max(0, self.offset + delta * 100)
            self.refresh()

    def inspect(self, row, column):
        if self.worker or not 0 <= row < len(self.rows):
            return
        track, service, result = self.rows[row]
        if service == 'domestic':
            from .domestic_dialog import DomesticReviewDialog
            DomesticReviewDialog(self.library, self.settings, track, result, self).exec()
            self.refresh()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('찾은 곡 비교·선택')
        dialog.resize(900, 660)
        layout = QVBoxLayout(dialog)
        details = QTextEdit()
        details.setReadOnly(True)
        lines = [f"곡: {track['title']} · {track['artist']}", f"조회 사이트: {PROVIDERS.get(service, service)}"]
        if result:
            lines.append('조회 안내: ' + readable(result.get('reason', '')))
            if result.get('tags'):
                lines.append('참고 분류: ' + ' · '.join(str(tag) for tag in result['tags']))
            for item in result.get('evidence', []):
                lines.append(' · '.join(str(item[key]) for key in ('title', 'artist', 'album', 'tags', 'match_reason') if item.get(key)))
            for candidate in result.get('candidates', []):
                lines.append('\n찾은 곡: ' + ' · '.join(str(candidate.get(key, '')) for key in ('title', 'artist') if candidate.get(key)))
                lines.append('앨범: ' + (' / '.join(candidate.get('albums', [])) or '정보 없음'))
        else:
            lines.append('아직 조회한 정보가 없습니다. 먼저 곡 정보 찾기를 실행하세요.')
        details.setPlainText('\n'.join(lines))
        tabs = QTabWidget()
        tabs.addTab(details, '찾은 곡 정보')
        raw = QTextEdit()
        raw.setReadOnly(True)
        raw.setPlainText(json.dumps(dict(file=dict(title=track['title'], artist=track['artist'], album=track['album'], version=track['version'], duration=track['duration']),
                                        service=service, result=result), ensure_ascii=False, indent=2))
        tabs.addTab(raw, '기술 상세 (JSON)')
        layout.addWidget(tabs, 1)
        choices = QComboBox()
        for candidate in (result or {}).get('candidates', []):
            choices.addItem(candidate['title'] + ' · ' + candidate['artist'] + ' · ' + (' / '.join(candidate.get('albums', [])) or '앨범 정보 없음'), candidate['recording_id'])
        layout.addWidget(choices)
        reason = QLineEdit()
        reason.setMaxLength(600)
        reason.setPlaceholderText('이 곡이 맞다고 판단한 이유 (가수·앨범·곡 버전 등)')
        layout.addWidget(reason)
        confirmed = QCheckBox('선택한 정보가 이 파일과 같은 곡임을 확인했습니다')
        layout.addWidget(confirmed)
        status = QLabel('선택한 곡 정보를 AI 분류의 참고 자료로 저장합니다. 음악 파일과 현재 분류는 바꾸지 않습니다.')
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QHBoxLayout()
        select = QPushButton('선택한 곡 정보 저장')
        select.setProperty('primary', True)
        select.setEnabled(service == 'musicbrainz' and choices.count() > 0)
        def save():
            if not confirmed.isChecked():
                status.setText('같은 곡인지 확인한 뒤 체크하세요.')
                return
            try:
                ExternalLookup(self.library, self.settings).choose_candidate(track['id'], choices.currentData(), result, track['revision'], reason.text())
            except (ValueError, OSError) as error:
                status.setText(str(error))
                return
            dialog.accept()
            self.refresh()
        select.clicked.connect(save)
        close = QPushButton('닫기')
        close.clicked.connect(dialog.reject)
        buttons.addWidget(select)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

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
            lookup = ExternalLookup(self.library, self.settings, CredentialStore().get('lastfm') or '', control=control)
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
                    services = ('domestic', 'musicbrainz', 'lastfm') if self.settings.domestic_enabled else ('musicbrainz', 'lastfm')
                    for service in services:
                        control.checkpoint()
                        try:
                            result = lookup.lookup(track, service, force)
                        except ValueError as error:
                            result = lookup.record_failure(track, service, str(error))
                        if result.get('state') == 'failed' or result.get('failures') or any(s.get('album_error') for s in result.get('sources', [])):
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
        for widget in (self.start_button, self.scope, self.force, self.close_button):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.stop.setVisible(True)
        self.worker.result.connect(self.result)
        self.worker.error.connect(self.status.setText)
        self.worker.progress.connect(lambda count, failed: self.status.setText(f'조회 {count:,}곡 · 오류 {failed:,}'))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def result(self, result):
        rows, count, failed = result
        self.status.setText(f'조회 {count:,}곡 · 오류 {failed:,} · 찾은 정보를 곡별로 확인할 수 있습니다.')

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        for widget in (self.start_button, self.scope, self.force, self.close_button):
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.stop.setVisible(False)
        self.refresh()

    def reject(self):
        if self.worker:
            self.status.setText('조회 중입니다. 「조회 중단」을 누른 뒤 종료를 기다려 주세요.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
