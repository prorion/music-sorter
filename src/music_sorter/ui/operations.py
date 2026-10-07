from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                              QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..file_ops import FileOperations
from ..scanner import ScanControl


class OperationWorker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action, self.control = action, ScanControl()

    def run(self):
        try:
            self.result.emit(self.action(self.control, lambda a, b: self.progress.emit(a, b)))
        except (ValueError, OSError) as error:
            self.error.emit(str(error) if isinstance(error, ValueError) else '파일 접근 실패. 작업 기록과 경로를 확인하세요.')
        except Exception:
            self.error.emit('작업 중 오류가 발생했습니다. 완료 결과와 복구 자료는 보존했습니다.')


class FileDialog(QDialog):
    def __init__(self, library, settings, selected, filters, parent=None, job_id=None):
        super().__init__(parent)
        self.library, self.settings, self.selected, self.filters = library, settings, selected, filters
        self.engine = FileOperations(library, Path(settings.music_root))
        self.worker, self.job_id = None, job_id
        self.offset = 0
        self.setWindowTitle('파일 정리 · 미리보기와 복구')
        self.resize(1180, 760)
        layout = QVBoxLayout(self)
        title = QLabel('파일 정리')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        note = QLabel('실제 적용은 아래 버튼을 눌렀을 때 시작합니다. 음악 파일을 삭제하지 않습니다.\n변경한 경로·ID3는 작업 이력에서 되돌릴 수 있습니다. 미확정 곡의 기존 장르는 유지합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.form = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem(f'선택한 {len(selected)}곡', 'selected')
        self.scope.addItem('검색·필터 결과 전체', 'filtered')
        self.scope.addItem('라이브러리 전체', 'all')
        self.form.addWidget(self.scope)
        self.checks = {}
        for key, text in (('organize', '폴더 정리'), ('rename', '이름 변경'), ('write_genre', 'ID3 장르 기록'), ('archive_duplicates', '중복 후보 폴더 보관')):
            check = QCheckBox(text)
            self.form.addWidget(check)
            self.checks[key] = check
            check.toggled.connect(self.invalidate)
        self.scope.currentIndexChanged.connect(self.invalidate)
        layout.addLayout(self.form)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['상태', '현재 경로', '변경 경로', '장르', '안내'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, width in enumerate((100, 300, 300, 70, 250)):
            self.table.setColumnWidth(i, width)
        layout.addWidget(self.table, 1)
        pages = QHBoxLayout()
        self.previous = QPushButton('이전 페이지')
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.page_label = QLabel()
        self.next = QPushButton('다음 페이지')
        self.next.clicked.connect(lambda: self.turn_page(1))
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.status = QLabel('실행할 항목을 선택하고 미리보기를 만드세요.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.preview_button = QPushButton('미리보기 만들기')
        self.preview_button.clicked.connect(self.preview)
        self.apply_button = QPushButton('확인한 파일 변경 실제 적용')
        self.apply_button.setProperty('primary', True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        self.resume_button = QPushButton('중단 작업 확인·재개')
        self.resume_button.clicked.connect(lambda: self.start(lambda *_: self.engine.resume(self.job_id), self.applied))
        self.undo_button = QPushButton('이 작업 되돌리기')
        self.undo_button.clicked.connect(self.undo)
        self.stop_button = QPushButton('작업 취소')
        self.stop_button.clicked.connect(lambda: self.worker.control.cancelled.set() if self.worker else None)
        self.stop_button.setEnabled(False)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        for widget in (self.preview_button, self.apply_button, self.resume_button, self.undo_button, self.stop_button, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        if job_id:
            self.load()
        else:
            self.resume_button.setEnabled(False)
            self.undo_button.setEnabled(False)

    def busy(self):
        return self.worker is not None

    def invalidate(self):
        if self.busy():
            return
        self.job_id = None
        self.offset = 0
        self.table.setRowCount(0)
        self.status.setText('조건이 바뀌었습니다. 새 미리보기를 만드세요.')
        self.apply_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.undo_button.setEnabled(False)

    def start(self, action, callback):
        if self.busy():
            return
        self.worker = OperationWorker(action, self)
        self.scope.setEnabled(False)
        for check in self.checks.values():
            check.setEnabled(False)
        for button in (self.preview_button, self.apply_button, self.resume_button, self.undo_button):
            button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker.result.connect(callback)
        self.worker.error.connect(self.status.setText)
        self.worker.progress.connect(lambda count, blocked: self.status.setText(f'처리 {count:,} · 보류 {blocked:,}'))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        self.scope.setEnabled(True)
        for check in self.checks.values():
            check.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.load()
        worker.deleteLater()

    def preview(self):
        if self.scope.currentData() == 'selected':
            ids = list(self.selected)
        else:
            clause, params = self.library._filter_clause(**(self.filters if self.scope.currentData() == 'filtered' else {}))
            with self.library.connection() as db:
                ids = [row[0] for row in db.execute('SELECT id FROM tracks' + clause + ' ORDER BY id', params)]
        if not ids:
            self.status.setText('대상 곡이 없습니다.')
            return
        options = {key: check.isChecked() for key, check in self.checks.items()}
        self.start(lambda control, progress: self.engine.preview(ids, **options, tolerance=self.settings.duplicate_tolerance_seconds,
                                                                 control=control, progress=progress), self.previewed)

    def previewed(self, job_id):
        self.job_id = job_id
        self.offset = 0
        self.status.setText('미리보기 완료. 적용 대상과 보류 이유를 확인하세요.')

    def load(self):
        rows = self.engine.operations(self.job_id, 200, self.offset) if self.job_id else []
        counts = self.engine.operation_counts(self.job_id) if self.job_id else {}
        total = sum(counts.values())
        self.table.setRowCount(len(rows))
        states = dict(planned='적용 가능', blocked='보류', unchanged='변경 없음', prepared='준비됨',
                      tag_done='태그 완료', file_done='파일 완료', recorded='적용 완료', undone='복구 완료', undo_prepared='복구 확인 필요')
        for i, row in enumerate(rows[:200]):
            plan = row['plan']
            values = [states.get(row['state'], row['state']), plan['path'], plan['destination'], plan['genre'] or '유지',
                      ' · '.join(plan['notes'] + ([row['reason']] if row['reason'] else []))]
            for j, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(i, j, item)
        self.apply_button.setEnabled(bool(counts.get('planned')) and not self.busy())
        self.undo_button.setEnabled(bool(counts.get('recorded')) and not self.busy())
        self.resume_button.setEnabled(any(counts.get(state) for state in ('prepared', 'tag_done', 'file_done')) and not self.busy())
        self.previous.setEnabled(self.offset > 0 and not self.busy())
        self.next.setEnabled(self.offset + len(rows) < total and not self.busy())
        self.page_label.setText(f'{total:,}개 계획 · 적용 가능 {counts.get("planned", 0):,} · 보류 {counts.get("blocked", 0):,} · 페이지 {self.offset // 200 + 1}')

    def turn_page(self, direction):
        self.offset = max(0, self.offset + direction * 200)
        self.load()

    def apply(self):
        if QMessageBox.question(self, '실제 파일 변경', '확인한 계획대로 음악 파일의 위치·이름·장르를 변경할까요?\n완료 변경은 작업 이력에서 되돌릴 수 있습니다.', defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.start(lambda control, progress: self.engine.apply(self.job_id, control, progress), self.applied)

    def applied(self, result):
        self.status.setText(f'완료 {result.get("completed", 0):,} · 보류 {result.get("blocked", 0):,}' + (' · 취소됨' if result.get('cancelled') else ''))

    def undo(self):
        rows = [row for row in self.engine.operations(self.job_id) if row['state'] == 'recorded']
        affected = sum(len(self.engine.undo_preview(row['id'])) for row in rows)
        if QMessageBox.question(self, '파일 변경 되돌리기', f'이 작업과 의존하는 후속 파일 변경을 역순으로 복구합니다.\n복구 계획 최대 {affected}개. 외부 수정·경로 충돌은 보류합니다. 진행할까요?', defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        def action(control, progress):
            done = 0
            for row in reversed(rows):
                control.checkpoint()
                self.engine.undo(row['id'])
                done += 1
                progress(done, 0)
            return {'completed': done, 'blocked': 0}
        self.start(action, self.applied)

    def reject(self):
        if self.busy():
            self.worker.control.cancelled.set()
            self.status.setText('취소 요청 · 현재 파일의 안전한 완료 지점까지 기다려 주세요.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
