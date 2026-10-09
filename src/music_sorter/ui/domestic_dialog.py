from datetime import datetime

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QHeaderView, QWidget)

from ..domestic import DomesticLookup, SITES, STATES, canonical_url
from .operations import OperationWorker


class DomesticReviewDialog(QDialog):
    def __init__(self, library, settings, track, result, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.track, self.result = library, settings, track, result or {}
        self.worker = None
        self.setWindowTitle('국내 곡 정보 · 출처 비교')
        self.resize(1160, 740)
        layout = QVBoxLayout(self)
        title = QLabel('여러 출처에서 찾은 곡 정보')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        identity = QLabel(f"{track['title']} · {track['artist']}\n파일의 앨범: {track['album'] or '정보 없음'}")
        identity.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(identity)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['출처', '제목 · 아티스트', '앨범', '장르', '정보 범위', '발매일', '같은 곡 확인'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((65, 220, 145, 100, 125, 100)):
            self.table.setColumnWidth(index, width)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_source())
        layout.addWidget(self.table, 1)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_label)
        self.table.currentCellChanged.connect(lambda *_: self.source_changed())
        source_actions = QHBoxLayout()
        self.open_button = QPushButton('선택한 출처 열기')
        self.open_button.setProperty('primary', True)
        self.open_button.clicked.connect(self.open_source)
        source_actions.addWidget(self.open_button)
        self.add_toggle = QCheckBox('출처 직접 추가')
        source_actions.addWidget(self.add_toggle)
        source_actions.addStretch()
        layout.addLayout(source_actions)
        self.add_form = QWidget()
        add = QHBoxLayout(self.add_form)
        add.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit()
        self.url.setPlaceholderText('추가로 대조할 멜론·벅스·지니 곡 상세 URL (최대 3개)')
        self.add_button = QPushButton('출처 추가·다시 대조')
        self.add_button.clicked.connect(lambda: self.start(True))
        add.addWidget(self.url, 1)
        add.addWidget(self.add_button)
        layout.addWidget(self.add_form)
        self.add_form.setVisible(False)
        self.add_toggle.toggled.connect(self.add_form.setVisible)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel('검색·대조는 프로그램이 수행하며 AI 비용이 들지 않습니다. 사이트 수는 독립적인 원자료 수나 정답 확률이 아닙니다.\n곡 정보와 앨범 정보의 범위를 구분합니다. 조회만으로 현재 분류·음악 파일을 변경하지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton('최신 정보로 다시 대조')
        self.refresh_button.clicked.connect(lambda: self.start(False))
        self.stop = QPushButton('조회 중단')
        self.stop.setEnabled(False)
        self.stop.setVisible(False)
        self.stop.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        self.close_button = QPushButton('닫기')
        self.close_button.clicked.connect(self.reject)
        for button in (self.refresh_button, self.stop, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.populate()

    def populate(self):
        state = self.result.get('state')
        genres = ' · '.join(self.result.get('agreed_genres', []))
        self.summary.setText((STATES.get(state, '아직 조회하지 않았습니다.')) + (f' · 확인된 참고 장르: {genres}' if genres else '') +
                             f"\n같은 곡 후보를 찾은 사이트 {self.result.get('source_count', 0)}곳 · 곡 장르 대조 가능 {self.result.get('genre_source_count', 0)}곳")
        self.sources = self.result.get('sources', [])
        self.table.setRowCount(len(self.sources))
        scopes = {'track': '곡 정보', 'single_album': '한 곡짜리 앨범', 'album_reference': '앨범 전체 참고'}
        for i, source in enumerate(self.sources):
            values = (SITES[source['site']], source['title'] + ' · ' + source['artist'], source['album'],
                      ' · '.join(source.get('genres') or source.get('album_genres') or []) or '정보 없음',
                      scopes.get(source['genre_scope'], source['genre_scope']), source.get('release_date') or '정보 없음',
                      '후보 일치' if source.get('accepted') else source.get('match_reason', '확인 필요'))
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(i, j, item)
        problems = [' · '.join((SITES.get(row['site'], row['site']), row['reason'])) for row in self.result.get('failures', [])]
        self.status.setText('\n'.join(problems) if problems else '출처를 선택하면 상세 주소·조회 시점·비교 이유를 확인할 수 있습니다.')
        if self.sources:
            self.table.setCurrentCell(0, 0)
        self.source_changed()

    def source_changed(self):
        index = self.table.currentRow()
        enabled = 0 <= index < len(self.sources)
        self.open_button.setEnabled(enabled)
        if enabled:
            source = self.sources[index]
            try:
                stamp = datetime.fromisoformat(source.get('queried_at', '')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                stamp = '정보 없음'
            self.source_label.setText(f"출처: {source['url']}\n확인 시점: {stamp} (현재 시간대) · {source.get('match_reason', '')}" +
                                      (f"\n출처에 표시된 장르: {source.get('raw_genre') or source.get('album_raw_genre')}" if source.get('raw_genre') or source.get('album_raw_genre') else '') +
                                      (f"\n장르의 앨범 출처: {source['album_url']}" if source.get('album_url') else '') +
                                      (f"\n앨범 정보 조회 안내: {source['album_error']}" if source.get('album_error') else ''))
        else:
            self.source_label.setText('선택한 출처가 없습니다.')

    def open_source(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.sources):
            _, url = canonical_url(self.sources[index]['url'])
            QDesktopServices.openUrl(QUrl(url))

    def start(self, add):
        if self.worker:
            return
        extras = list(self.result.get('extra_urls', []))
        try:
            if add:
                _, url = canonical_url(self.url.text().strip())
                if '/album/' in url:
                    raise ValueError('앨범 전체가 아닌 곡 상세 URL을 입력하세요.')
                extras = list(dict.fromkeys([*extras, url]))
            if len(extras) > 3:
                raise ValueError('추가 출처는 최대 3개입니다.')
            current = self.library.track(self.track['id'])
            if current['revision'] != self.track['revision'] or current['file_state'] != 'ready':
                raise ValueError('곡 정보가 바뀌었습니다. 창을 닫고 다시 열어 주세요.')
        except ValueError as error:
            self.status.setText(str(error))
            return
        def action(control, progress):
            return DomesticLookup(self.library, self.settings, control=control).lookup(self.track, True, extras)
        self.worker = OperationWorker(action, self)
        for widget in (self.url, self.add_button, self.refresh_button, self.close_button):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.stop.setVisible(True)
        self.status.setText('공개 곡 정보를 조회하고 항목별로 대조하는 중입니다…')
        self.worker.result.connect(self.received)
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def received(self, result):
        self.result = result
        self.url.clear()
        self.populate()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        for widget in (self.url, self.add_button, self.refresh_button, self.close_button):
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.stop.setVisible(False)

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
