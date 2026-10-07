import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)

from ..classifier import Classifier
from ..classification import LABELS
from ..llm import ProviderClient
from ..settings import CredentialStore
from .operations import OperationWorker


class ClassifyDialog(QDialog):
    def __init__(self, library, settings, selected, filters, parent=None, job_id=None):
        super().__init__(parent)
        self.library, self.settings = library, settings
        self.selected, self.filters = selected, filters
        self.engine, self.job_id, self.worker = Classifier(library), job_id, None
        self.offset = 0
        self.setWindowTitle('음악 분류 · 실행 계획과 결과')
        self.resize(1100, 780)
        layout = QVBoxLayout(self)
        heading = QLabel('음악 분류')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel('입력 계획을 확인한 뒤 유료 제출합니다. 파일 자체·커버·전체 경로는 전송하지 않습니다.\n모델 신뢰도는 자기 평가이며 정답률 검증은 진행 전입니다. 분류 결과는 DB에만 반영합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QHBoxLayout()
        self.scope, self.purpose, self.execution = QComboBox(), QComboBox(), QComboBox()
        for text, value in ((f'선택한 {len(selected)}곡', 'selected'), ('검색·필터 전체', 'filtered'), ('라이브러리 전체', 'all')):
            self.scope.addItem(text, value)
        for text, value in (('미분류 곡 1차 분류', 'classify'), ('미확정 항목 재판정', 'escalate'), ('완료 곡 포함 재분류', 'reclassify')):
            self.purpose.addItem(text, value)
        self.execution.addItem('동기 · 즉시 처리', 'sync')
        self.execution.addItem('Batch · 원격 비동기', 'batch')
        self.budget = QLineEdit()
        self.budget.setPlaceholderText('이번 작업의 USD 예산')
        self.budget.setMaximumWidth(210)
        self.lyrics = QCheckBox('재판정: 기존 ID3 가사 최대 2,000자')
        for widget in (self.scope, self.purpose, self.execution, self.budget):
            form.addWidget(widget)
        layout.addLayout(form)
        layout.addWidget(self.lyrics)
        self.model_label = QLabel()
        layout.addWidget(self.model_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['곡', '상태', '결과·검토 안내', '입력 확인'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 250)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 330)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self.inspect)
        layout.addWidget(self.table, 1)
        pages = QHBoxLayout()
        self.previous, self.next = QPushButton('이전'), QPushButton('다음')
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next.clicked.connect(lambda: self.turn_page(1))
        pages.addWidget(self.previous)
        self.abandon_button = QPushButton('미제출 계획 취소')
        self.abandon_button.clicked.connect(self.abandon)
        pages.addWidget(self.abandon_button)
        self.resolve_button = QPushButton('처리 불확실 요청 확인')
        self.resolve_button.clicked.connect(self.resolve_unknown)
        pages.addWidget(self.resolve_button)
        pages.addStretch()
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.status = QLabel('예산·대상·목적을 선택하고 계획을 준비하세요. 준비 버튼은 유료 요청을 보내지 않습니다.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.prepare_button = QPushButton('전송 입력·비용 계획 준비')
        self.prepare_button.clicked.connect(self.prepare)
        self.run_button = QPushButton('계획 확인 후 유료 제출·재개')
        self.run_button.setProperty('primary', True)
        self.run_button.clicked.connect(self.run)
        self.collect_button = QPushButton('Batch 상태·결과 확인')
        self.collect_button.clicked.connect(lambda: self.remote('collect'))
        self.cancel_remote_button = QPushButton('원격 취소 요청')
        self.cancel_remote_button.clicked.connect(lambda: self.remote('cancel'))
        self.retry_button = QPushButton('확인된 실패 재시도 준비')
        self.retry_button.clicked.connect(self.retry)
        self.stop_button = QPushButton('작업 중단')
        self.stop_button.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for widget in (self.prepare_button, self.run_button, self.collect_button, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        controls = QHBoxLayout()
        for widget in (self.cancel_remote_button, self.retry_button, self.stop_button):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)
        self.conditional_buttons = (self.run_button, self.collect_button, self.cancel_remote_button, self.retry_button, self.resolve_button)
        self.controls = (self.scope, self.purpose, self.execution, self.budget, self.lyrics, self.prepare_button)
        self.stop_button.setEnabled(False)
        self.purpose.currentIndexChanged.connect(self.model_changed)
        for combo in (self.scope, self.purpose, self.execution):
            combo.currentIndexChanged.connect(self.invalidate)
        self.lyrics.toggled.connect(self.invalidate)
        self.model_changed()
        defaults = CredentialStore.profile_defaults
        if not job_id:
            if defaults.get('budget'):
                self.budget.setText(defaults['budget'])
            if defaults.get('execution'):
                self.execution.setCurrentIndex(self.execution.findData(defaults['execution']))
        if job_id:
            options = self.engine.job(job_id)['options']
            self.purpose.blockSignals(True)
            self.execution.blockSignals(True)
            self.lyrics.blockSignals(True)
            self.purpose.setCurrentIndex(self.purpose.findData(options['purpose']))
            self.execution.setCurrentIndex(self.execution.findData(options['execution']))
            self.lyrics.setChecked(options['include_lyrics'])
            for widget in (self.purpose, self.execution, self.lyrics):
                widget.blockSignals(False)
            self.budget.setText(str(self.engine.job(job_id)['budget'] / 1000000))
        self.load()

    def model_changed(self):
        escalate = self.purpose.currentData() == 'escalate'
        self.lyrics.setEnabled(escalate)
        if not self.job_id:
            self.lyrics.setChecked(escalate and CredentialStore.profile_defaults.get('include_lyrics', self.settings.include_lyrics_default))
        self.model_label.setText(f"{self.settings.escalate_provider if escalate else self.settings.classify_provider} · "
                                 f"{self.settings.escalate_model if escalate else self.settings.classify_model}")

    def invalidate(self):
        if self.worker or self.job_id:
            # An existing plan holds its original options. New work needs a fresh dialog.
            if self.job_id:
                self.status.setText('기존 작업은 시작 시 설정을 유지합니다. 새 목적·모델은 창을 닫고 새 계획으로 실행하세요.')
            return

    def prepare(self):
        if self.worker:
            return
        if self.scope.currentData() == 'selected':
            ids = list(self.selected)
        else:
            clause, params = self.library._filter_clause(**(self.filters if self.scope.currentData() == 'filtered' else {}))
            with self.library.connection() as db:
                ids = [r[0] for r in db.execute('SELECT id FROM tracks' + clause + ' ORDER BY id', params)]
        if not ids:
            self.status.setText('선택 범위에 곡이 없습니다.')
            return
        escalate = self.purpose.currentData() == 'escalate'
        options = dict(provider=self.settings.escalate_provider if escalate else self.settings.classify_provider,
                       model=self.settings.escalate_model if escalate else self.settings.classify_model,
                       budget=self.budget.text(), purpose=self.purpose.currentData(), execution=self.execution.currentData(),
                       workspace=self.settings.anthropic_workspace_id,
                       include_lyrics=escalate and self.lyrics.isChecked())
        options.update(tracks_per_request=self.settings.llm_tracks_per_request,
                       max_output_tokens_per_track=self.settings.llm_max_output_tokens_per_track,
                       timeout_seconds=self.settings.llm_timeout_seconds, max_retries=self.settings.llm_max_retries)
        from ..external import ExternalLookup
        options['external'] = ExternalLookup(self.library, self.settings).evidence
        self.start(lambda control, progress: self.engine.prepare(ids, **options, control=control, progress=progress), self.prepared)

    def prepared(self, job_id):
        self.job_id = job_id
        self.offset = 0
        self.status.setText('계획 준비 완료. 각 곡을 두 번 클릭하면 실제 전송 입력·가사·제안을 확인할 수 있습니다.')

    def start(self, action, callback=None):
        if self.worker:
            return
        self.worker = OperationWorker(action, self)
        for widget in (*self.controls, *self.conditional_buttons):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker.result.connect(callback or (lambda _: self.load()))
        self.worker.error.connect(self.status.setText)
        self.worker.progress.connect(lambda count, failed: self.status.setText(f'처리 {count:,} · 보류/실패 {failed:,}'))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.stop_button.setEnabled(False)
        self.load()

    def with_client(self, action, control, progress):
        job = self.engine.job(self.job_id)
        provider = job['options']['provider']
        client = ProviderClient(provider, CredentialStore().get(provider), job['options'].get('workspace', ''), timeout=job['options'].get('timeout_seconds', 60))
        try:
            return action(self.job_id, client, control=control, progress=progress)
        finally:
            client.close()

    def run(self):
        if self.worker or not self.job_id:
            return
        try:
            self.engine.increase_budget(self.job_id, self.budget.text())
            item = self.engine.summary(self.job_id)
        except ValueError as error:
            self.status.setText(str(error))
            return
        options = item['options']
        text = (f"{sum(item['counts'].values())}곡 · {options['provider']} / {options['model']} · {options['execution']}\n"
                f"작업 예산 US${item['budget']/1000000:.6f} · 계산된 제출 예약 약 US${item['reservation_estimate']/1000000:.6f}\n"
                f"집계 US${item['actual']/1000000:.6f} · 미완료 예약 US${item['reserved']/1000000:.6f}\n"
                '입력·가격·가사 범위를 확인했으며 유료 요청을 제출할까요?')
        if QMessageBox.question(self, '유료 분류 제출', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        action = self.engine.submit_batch if options['execution'] == 'batch' else self.engine.run
        self.start(lambda control, progress: self.with_client(action, control, progress))

    def remote(self, kind):
        if not self.job_id or self.worker:
            return
        if kind == 'collect':
            self.start(lambda c, p: self.with_client(self.engine.collect_batch, c, p))
        else:
            def action(control, progress):
                return self.with_client(lambda job, client, **_: self.engine.cancel_remote(job, client), control, progress)
            self.start(action)

    def retry(self):
        if self.job_id and not self.worker:
            count = self.engine.retry_failed(self.job_id)
            maximum = self.engine.job(self.job_id)['options'].get('max_retries', 3) + 1
            self.status.setText(f'확인된 실패 {count}곡을 재시도 준비했습니다. 총 {maximum}회 한도·예산을 유지합니다. 유료 제출 버튼으로 실행하세요.')
            self.load()

    def load(self):
        self.rows = self.engine.rows(self.job_id, limit=200, offset=self.offset) if self.job_id else []
        self.table.setRowCount(len(self.rows))
        states = dict(prepared='미제출', blocked='제출 보류', cancelled='취소', sending='전송 중', unknown='처리 확인 필요', received='응답 수신', remote='원격 처리', completed='완료', failed='실패', proposal='검토 제안')
        for i, row in enumerate(self.rows):
            inputs = json.loads(row['input'])
            for j, value in enumerate((inputs['title'] + ' · ' + inputs['artist'], states.get(row['state'], row['state']), row['reason'], '두 번 클릭 · 입력 JSON/가사/근거')):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(i, j, item)
        if self.job_id:
            summary = self.engine.summary(self.job_id)
            warning = ' · 예산 80% 이상' if summary['actual'] + summary['reserved'] >= summary['budget'] * .8 else ''
            self.model_label.setText(f"{summary['options']['provider']} · {summary['options']['model']} · {summary['options']['execution']} | "
                                     f"집계 US${summary['actual']/1000000:.6f} · 미완료 예약 US${summary['reserved']/1000000:.6f} · "
                                     f"예산 US${summary['budget']/1000000:.6f}{warning}")
            self.run_button.setEnabled(bool(summary['counts'].get('prepared')) and not self.worker)
            batch = summary['options']['execution'] == 'batch'
            self.collect_button.setEnabled(batch and bool(summary['counts'].get('remote')) and not self.worker)
            self.cancel_remote_button.setEnabled(self.collect_button.isEnabled())
            self.retry_button.setEnabled(bool(summary['counts'].get('failed')) and not self.worker)
            self.previous.setEnabled(self.offset > 0)
            self.next.setEnabled(self.offset + len(self.rows) < sum(summary['counts'].values()))
            self.abandon_button.setEnabled(bool(summary['counts'].get('prepared')) and not self.worker)
            self.resolve_button.setEnabled(bool(summary['counts'].get('unknown')) and not self.worker)
        else:
            self.abandon_button.setEnabled(False)
            for button in self.conditional_buttons:
                button.setEnabled(False)
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
        for widget in self.controls:
            widget.setEnabled(not self.worker and (not self.job_id or widget is self.budget))
        if not self.job_id:
            self.lyrics.setEnabled(self.purpose.currentData() == 'escalate')

    def inspect(self, row, column):
        if row >= len(self.rows):
            return
        target = self.rows[row]
        dialog = QDialog(self)
        dialog.setWindowTitle('전송 입력·분류 응답·검토 이유')
        dialog.resize(850, 640)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(dict(input=json.loads(target['input']), result=json.loads(target['result']) if target['result'] else None,
                                         reason=target['reason']), ensure_ascii=False, indent=2))
        layout.addWidget(text)
        close = QPushButton('닫기')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def abandon(self):
        if self.job_id and not self.worker:
            count = self.engine.cancel_prepared(self.job_id)
            self.status.setText(f'미제출 {count}곡 계획을 취소했습니다. 새 계획을 만들 수 있으며 기존 원격 요청·비용 예약은 유지합니다.')
            self.load()

    def turn_page(self, delta):
        self.offset = max(0, self.offset + delta * 200)
        self.load()

    def resolve_unknown(self):
        if self.worker or not self.job_id:
            return
        requests = self.engine.uncertain_requests(self.job_id)
        if not requests:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('처리 여부 확인 · 미완료 예약')
        dialog.resize(920, 580)
        layout = QVBoxLayout(dialog)
        note = QLabel('제공자의 요청 기록·과금 내역에서 미처리와 미과금을 직접 확인한 요청만 선택하세요.\n확인할 수 없거나 이미 처리됐으면 예약을 유지합니다. 이 화면은 재전송하지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(requests), 4)
        table.setHorizontalHeaderLabels(['선택·요청 ID', '원격 ID', '예약 USD', '요청 시각'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 330)
        for i, request in enumerate(requests):
            item = QTableWidgetItem(request['id'])
            item.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(i, 0, item)
            for j, value in enumerate((request['remote_id'] or '미확인', f"{request['reserved']/1000000:.6f}", request['created_at']), 1):
                table.setItem(i, j, QTableWidgetItem(value))
        layout.addWidget(table, 1)
        confirmed = QCheckBox('선택한 요청의 미처리·미과금을 제공자에서 확인했습니다')
        layout.addWidget(confirmed)
        reason = QLineEdit()
        reason.setMaxLength(600)
        reason.setPlaceholderText('확인 근거·시각·문의 번호 (API 키 입력 제외)')
        layout.addWidget(reason)
        status = QLabel('확인 완료한 요청의 예약만 해제합니다. 새 유료 작업은 별도 계획이 필요합니다.')
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QHBoxLayout()
        apply = QPushButton('확인 기록 저장·예약 해제')
        close = QPushButton('예약 유지·닫기')
        close.clicked.connect(dialog.reject)
        def save():
            ids = [request['id'] for i, request in enumerate(requests) if table.item(i, 0).checkState() == Qt.CheckState.Checked]
            try:
                result = self.engine.confirm_unprocessed(self.job_id, ids, reason.text(), confirmed=confirmed.isChecked())
            except ValueError as error:
                status.setText(str(error))
                return
            self.status.setText(f"확인 {result['requests']}요청 · 예약 US${result['released']/1000000:.6f} 해제 · 자동 재전송 없음")
            dialog.accept()
            self.load()
        apply.clicked.connect(save)
        buttons.addWidget(apply)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def reject(self):
        if not self.worker:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            super().closeEvent(event)
