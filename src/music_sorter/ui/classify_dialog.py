import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QPushButton, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)

from ..classifier import Classifier
from ..classification import LABELS
from ..llm import ProviderClient
from ..settings import CredentialStore
from .operations import OperationWorker
from .wording import EXECUTIONS, PROVIDERS, classification_detail, readable
from .workflow import task_guide


class ClassifyDialog(QDialog):
    def __init__(self, library, settings, selected, filters, parent=None, job_id=None):
        super().__init__(parent)
        self.library, self.settings = library, settings
        self.selected, self.filters = selected, filters
        self.engine, self.job_id, self.worker = Classifier(library), job_id, None
        self.offset = 0
        self.setWindowTitle('AI 음악 분류')
        self.resize(1100, 780)
        layout = QVBoxLayout(self)
        heading = QLabel('AI 음악 분류')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        note = QLabel('곡 정보를 AI에 보내 장르·분위기·컨셉을 분류합니다. 실행할 때 API 이용 요금이 발생합니다.\n분류 결과는 앱에 저장합니다. 음악 파일·앨범 이미지는 보내지 않으며 파일 이름이나 장르 정보도 바꾸지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        task_guide(layout, '곡·예산 선택 → 예상 비용 확인 → 분류 시작(유료). 결과를 받은 뒤 메인 4번에서 검토하세요.')
        options_group = QGroupBox('분류할 곡과 예산')
        form = QGridLayout(options_group)
        self.scope, self.purpose, self.execution = QComboBox(), QComboBox(), QComboBox()
        for text, value in ((f'선택한 {len(selected)}곡', 'selected'), ('검색 결과 전체', 'filtered'), ('등록된 모든 곡', 'all')):
            self.scope.addItem(text, value)
        for text, value in (('아직 분류하지 않은 곡 분류', 'classify'), ('확인 필요한 곡 다시 분류', 'escalate'), ('완료된 곡도 다시 분류', 'reclassify')):
            self.purpose.addItem(text, value)
        self.execution.addItem('바로 처리', 'sync')
        self.execution.addItem('나중에 결과 받기', 'batch')
        self.budget = QLineEdit()
        self.budget.setPlaceholderText('예: 1.00')
        self.budget.setToolTip('이번 작업에 사용할 예산을 미국 달러로 입력하세요. 실제 결제는 AI 서비스 계정에서 이루어집니다.')
        self.lyrics = QCheckBox('다시 분류할 때 파일에 저장된 가사도 보내기 (최대 2,000자)')
        for column, (title, widget) in enumerate(zip(('분류할 곡', '할 일', '처리 방법', '예산 한도 (미국 달러)'),
                                                     (self.scope, self.purpose, self.execution, self.budget))):
            form.addWidget(QLabel(title), 0, column)
            form.addWidget(widget, 1, column)
            form.setColumnStretch(column, 1)
        self.execution_help = QLabel()
        self.execution_help.setWordWrap(True)
        form.addWidget(self.execution_help, 2, 0, 1, 4)
        form.addWidget(self.lyrics, 3, 0, 1, 4)
        layout.addWidget(options_group)
        self.model_label = QLabel()
        self.model_label.setWordWrap(True)
        layout.addWidget(self.model_label)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['No.', '곡', '진행 상태', '결과·확인이 필요한 이유', '상세 보기'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 250)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 310)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self.inspect)
        self.table_stack = QStackedWidget()
        self.empty_note = QLabel('AI에 보낼 정보와 예상 비용 확인\n\n곡과 예산을 선택하고 「예상 비용 확인」을 누르세요.\n이곳에 곡별 전송 정보와 처리 가능 여부가 표시됩니다.\n이 단계에서는 이용 요금이 발생하지 않습니다.')
        self.empty_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_note.setWordWrap(True)
        self.table_stack.addWidget(self.empty_note)
        self.table_stack.addWidget(self.table)
        layout.addWidget(self.table_stack, 1)
        pages = QHBoxLayout()
        self.previous, self.next = QPushButton('이전 페이지'), QPushButton('다음 페이지')
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next.clicked.connect(lambda: self.turn_page(1))
        pages.addWidget(self.previous)
        self.abandon_button = QPushButton('계획 취소')
        self.abandon_button.setToolTip('아직 AI에 보내지 않은 곡만 취소합니다. 이미 처리된 결과는 유지합니다.')
        self.abandon_button.clicked.connect(self.abandon)
        self.resolve_button = QPushButton('처리·결제 여부 확인')
        self.resolve_button.setToolTip('결과를 받지 못한 요청의 처리·결제 여부를 AI 서비스에서 직접 확인한 뒤 예상 비용을 해제할 수 있습니다.')
        self.resolve_button.clicked.connect(self.resolve_unknown)
        pages.addWidget(self.resolve_button)
        pages.addStretch()
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.status = QLabel('먼저 「예상 비용 확인」을 누르세요. 실제 분류는 「분류 시작 (유료)」에서 비용을 확인한 뒤 실행합니다.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.prepare_button = QPushButton('예상 비용 확인')
        self.prepare_button.clicked.connect(self.prepare)
        self.run_button = QPushButton('분류 시작 (유료)')
        self.run_button.setProperty('primary', True)
        self.run_button.clicked.connect(self.run)
        self.new_plan_button = QPushButton('다른 곡·조건 선택')
        self.new_plan_button.clicked.connect(self.new_plan)
        self.collect_button = QPushButton('진행 상황·결과 가져오기')
        self.collect_button.clicked.connect(lambda: self.remote('collect'))
        self.cancel_remote_button = QPushButton('서버에 처리 취소 요청')
        self.cancel_remote_button.setToolTip('서버에 취소를 요청합니다. 이미 처리된 곡에는 요금이 발생할 수 있으며 결과 확인으로 취소 여부를 확인합니다.')
        self.cancel_remote_button.clicked.connect(lambda: self.remote('cancel'))
        self.retry_button = QPushButton('실패한 곡 다시 준비')
        self.retry_button.clicked.connect(self.retry)
        self.stop_button = QPushButton('현재 작업 멈추기')
        self.stop_button.setToolTip('이 앱의 작업을 멈춥니다. 이미 서버에 보낸 작업은 별도로 취소를 요청해야 합니다.')
        self.stop_button.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        close = self.close_button = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for widget in (self.prepare_button, self.run_button, self.new_plan_button, self.collect_button, self.abandon_button, close):
            widget.setAutoDefault(False)
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        controls = QHBoxLayout()
        for widget in (self.cancel_remote_button, self.retry_button, self.stop_button):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)
        self.conditional_buttons = (self.run_button, self.new_plan_button, self.collect_button, self.cancel_remote_button, self.retry_button, self.resolve_button)
        self.controls = (self.scope, self.purpose, self.execution, self.budget, self.lyrics, self.prepare_button)
        self.stop_button.setEnabled(False)
        self.purpose.currentIndexChanged.connect(self.model_changed)
        self.execution.currentIndexChanged.connect(self.execution_changed)
        for combo in (self.scope, self.purpose, self.execution):
            combo.currentIndexChanged.connect(self.invalidate)
        self.lyrics.toggled.connect(self.invalidate)
        self.model_changed()
        self.execution_changed()
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
        if job_id:
            counts = self.engine.summary(job_id)['counts']
            labels = {'prepared': '시작 전', 'remote': '서버 처리 중', 'proposal': '직접 확인 필요', 'completed': '완료',
                      'cancelled': '취소', 'failed': '실패', 'blocked': '진행 불가', 'unknown': '처리 여부 확인 필요',
                      'sending': '요청 보내는 중', 'received': '결과 받음'}
            self.status.setText('저장된 작업 · ' + ' · '.join(f'{labels.get(state, state)} {count:,}곡' for state, count in counts.items()))

    def model_changed(self):
        escalate = self.purpose.currentData() == 'escalate'
        self.lyrics.setEnabled(escalate)
        if not self.job_id:
            self.lyrics.setChecked(escalate and CredentialStore.profile_defaults.get('include_lyrics', self.settings.include_lyrics_default))
        self.model_label.setText(f"사용할 AI: {PROVIDERS.get(self.settings.escalate_provider if escalate else self.settings.classify_provider)} · "
                                 f"{self.settings.escalate_model if escalate else self.settings.classify_model}")

    def execution_changed(self):
        self.execution_help.setText('바로 처리: 이 창에서 곡을 차례로 분류하고 결과를 확인합니다.'
                                    if self.execution.currentData() == 'sync' else
                                    '나중에 결과 받기: AI 서비스에 맡겨 처리합니다. 앱을 닫아도 서버 작업은 이어질 수 있습니다. 작업 이력에서 결과를 가져오세요.')

    def invalidate(self):
        if self.worker or self.job_id:
            # An existing plan holds its original options. New work needs a fresh dialog.
            if self.job_id:
                self.status.setText('이 작업은 준비할 때 선택한 곡·처리 방법·AI를 사용합니다. 다른 조건으로 분류하려면 창을 닫고 새 작업을 준비하세요.')
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
        self.status.setText('예상 비용 확인 완료. 곡을 두 번 클릭하면 AI에 보낼 정보를 볼 수 있습니다. 확인 후 「분류 시작 (유료)」를 누르세요.')
        if not self.engine.summary(job_id)['counts']:
            self.status.setText('선택한 조건에서 분류할 곡이 없습니다. 진행 중인 작업·분류 상태를 확인하거나 「다른 곡·조건 선택」을 누르세요.')

    def new_plan(self):
        if self.worker or not self.job_id:
            return
        counts = self.engine.summary(self.job_id)['counts']
        if any(counts.get(state) for state in ('prepared', 'remote', 'unknown', 'sending', 'received')):
            return
        self.job_id = None
        self.offset = 0
        self.scope.blockSignals(True)
        self.scope.setItemText(0, f'선택한 {len(self.selected)}곡')
        self.scope.blockSignals(False)
        self.load()
        self.model_changed()
        self.status.setText('새 대상과 조건을 선택하고 「예상 비용 확인」을 누르세요. 이전 작업 결과는 작업 기록에 남아 있습니다.')

    def start(self, action, callback=None):
        if self.worker:
            return
        self.worker = OperationWorker(action, self)
        for widget in (*self.controls, *self.conditional_buttons, self.abandon_button, self.previous, self.next):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.close_button.setEnabled(False)
        self.stop_button.show()
        self.worker.result.connect(callback or (lambda _: self.load()))
        self.worker.error.connect(lambda message: self.status.setText(readable(message)))
        self.worker.progress.connect(lambda count, failed: self.status.setText(f'처리 {count:,} · 보류/실패 {failed:,}'))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.stop_button.setEnabled(False)
        self.close_button.setEnabled(True)
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
            self.status.setText(readable(error))
            return
        options = item['options']
        text = (f"작업 대상: {sum(item['counts'].values())}곡\nAI: {PROVIDERS.get(options['provider'], options['provider'])} / {options['model']}\n처리 방법: {EXECUTIONS[options['execution']]}\n\n"
                f"예산 한도: US${item['budget']/1000000:.6f}\n이번에 보낼 요청의 예상 비용: 약 US${item['reservation_estimate']/1000000:.6f}\n"
                f"이미 사용한 비용: US${item['actual']/1000000:.6f}\n처리 중인 요청의 예상 비용: US${item['reserved']/1000000:.6f}\n\n"
                'AI 서비스 계정에 이용 요금이 발생합니다. 분류를 시작할까요?')
        if QMessageBox.question(self, 'AI 분류 시작 · 요금 확인', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
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
            self.status.setText(f'실패한 {count}곡을 다시 준비했습니다. 곡당 최대 {maximum}회·기존 예산 한도를 지킵니다. 「AI 분류 시작」을 눌러 실행하세요.')
            self.load()

    def load(self):
        self.rows = self.engine.rows(self.job_id, limit=200, offset=self.offset) if self.job_id else []
        self.table.setRowCount(len(self.rows))
        self.table_stack.setCurrentWidget(self.table if self.job_id else self.empty_note)
        states = dict(prepared='시작 전', blocked='진행 불가', cancelled='취소', sending='요청 보내는 중', unknown='처리 여부 확인 필요', received='결과 받음', remote='서버 처리 중', completed='완료', failed='실패', proposal='직접 확인 필요')
        for i, row in enumerate(self.rows):
            inputs = json.loads(row['input'])
            for j, value in enumerate((self.offset + i + 1, inputs['title'] + ' · ' + inputs['artist'], states.get(row['state'], row['state']), readable(row['reason']) or '—', '두 번 클릭: 보낼 정보·분류 결과')):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(i, j, item)
        if self.job_id:
            summary = self.engine.summary(self.job_id)
            self.scope.blockSignals(True)
            self.scope.setItemText(0, f"작업 대상 {sum(summary['counts'].values()):,}곡")
            self.scope.setCurrentIndex(0)
            self.scope.blockSignals(False)
            warning = ' · 예산 80% 이상' if summary['actual'] + summary['reserved'] >= summary['budget'] * .8 else ''
            self.model_label.setText(f"사용할 AI: {PROVIDERS.get(summary['options']['provider'], summary['options']['provider'])} · {summary['options']['model']}\n"
                                     f"이번 요청의 예상 비용 약 US${summary['reservation_estimate']/1000000:.6f}\n"
                                     f"사용한 비용 US${summary['actual']/1000000:.6f} · 처리 중인 예상 비용 US${summary['reserved']/1000000:.6f} · "
                                     f"예산 한도 US${summary['budget']/1000000:.6f}{warning}")
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
        counts = summary['counts'] if self.job_id else {}
        for button, visible in ((self.collect_button, self.job_id and batch and counts.get('remote')),
                                (self.cancel_remote_button, self.job_id and batch and counts.get('remote')),
                                (self.retry_button, counts.get('failed')), (self.resolve_button, counts.get('unknown')),
                                (self.abandon_button, counts.get('prepared')), (self.previous, self.offset > 0),
                                (self.next, self.offset + len(self.rows) < sum(counts.values()))):
            button.setVisible(bool(visible))
        self.stop_button.setVisible(bool(self.worker))
        self.run_button.setVisible(bool(counts.get('prepared')))
        can_restart = bool(self.job_id) and not any(counts.get(state) for state in ('prepared', 'remote', 'unknown', 'sending', 'received'))
        self.new_plan_button.setVisible(can_restart)
        self.new_plan_button.setEnabled(can_restart and not self.worker)
        # Preparation belongs to the initial form; subsequent runs use the saved plan.
        self.prepare_button.setVisible(not bool(self.job_id))
        self.prepare_button.setProperty('primary', not self.job_id)
        self.prepare_button.style().unpolish(self.prepare_button)
        self.prepare_button.style().polish(self.prepare_button)

    def inspect(self, row, column):
        if row >= len(self.rows):
            return
        target = self.rows[row]
        dialog = QDialog(self)
        dialog.setWindowTitle('곡 정보와 AI 분류 결과')
        dialog.resize(850, 640)
        layout = QVBoxLayout(dialog)
        inputs = json.loads(target['input'])
        result = json.loads(target['result']) if target['result'] else None
        tabs = QTabWidget()
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(classification_detail(inputs, result, target['reason']))
        tabs.addTab(text, '곡 정보·분류 결과')
        raw = QTextEdit()
        raw.setReadOnly(True)
        raw.setPlainText(json.dumps(dict(input=inputs, result=result, reason=target['reason']), ensure_ascii=False, indent=2))
        tabs.addTab(raw, '기술 상세 (JSON)')
        layout.addWidget(tabs)
        close = QPushButton('닫기')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def abandon(self):
        if self.job_id and not self.worker:
            count = self.engine.cancel_prepared(self.job_id)
            self.status.setText(f'아직 시작하지 않은 {count}곡을 취소했습니다. 이미 서버에 보낸 작업과 예상 비용은 그대로 유지합니다. 새 작업은 이 창을 닫고 준비하세요.')
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
        dialog.setWindowTitle('결과를 받지 못한 요청 · 처리·결제 확인')
        dialog.resize(920, 580)
        layout = QVBoxLayout(dialog)
        note = QLabel('AI 서비스의 요청 기록·결제 내역에서 「처리되지 않았고 요금도 발생하지 않았다」고 확인한 요청만 선택하세요.\n확인할 수 없으면 예상 비용을 그대로 남겨 둡니다. 이 화면에서 요청을 다시 보내지는 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(requests), 4)
        table.setHorizontalHeaderLabels(['선택·요청 번호', '서비스 요청 번호', '예상 비용 (미국 달러)', '요청 시각'])
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
        confirmed = QCheckBox('선택한 요청이 처리되지 않았고 요금도 없음을 AI 서비스에서 확인했습니다')
        layout.addWidget(confirmed)
        reason = QLineEdit()
        reason.setMaxLength(600)
        reason.setPlaceholderText('확인 근거·시각·문의 번호 (API 키 입력 제외)')
        layout.addWidget(reason)
        status = QLabel('확인된 요청의 예상 비용만 해제합니다. 다시 분류하려면 새 작업을 준비해야 합니다.')
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QHBoxLayout()
        apply = QPushButton('확인 내용 저장·예상 비용 해제')
        apply.setProperty('primary', True)
        close = QPushButton('예상 비용 유지·닫기')
        close.clicked.connect(dialog.reject)
        def save():
            ids = [request['id'] for i, request in enumerate(requests) if table.item(i, 0).checkState() == Qt.CheckState.Checked]
            try:
                result = self.engine.confirm_unprocessed(self.job_id, ids, reason.text(), confirmed=confirmed.isChecked())
            except ValueError as error:
                status.setText(readable(error))
                return
            self.status.setText(f"{result['requests']}건 확인 · 예상 비용 US${result['released']/1000000:.6f} 해제. 요청을 다시 보내지는 않았습니다.")
            dialog.accept()
            self.load()
        apply.clicked.connect(save)
        buttons.addWidget(apply)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def reject(self):
        if self.worker:
            self.status.setText('진행 중인 작업을 먼저 멈추고 완료를 기다려 주세요. 서버 처리 중인 작업은 별도로 취소를 요청해야 합니다.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
