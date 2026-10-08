from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..recycle import DuplicateRemoval
from .review_worker import ReviewWorker
from .player import Player


class DuplicateDialog(QDialog):
    def __init__(self, library, tolerance, parent=None, root=None):
        super().__init__(parent)
        self.library, self.tolerance = library, tolerance
        if root is None:
            with library.connection() as db:
                saved = db.execute("SELECT value FROM metadata WHERE key='music_root'").fetchone()
            root = saved[0] if saved else None
        self.removal = DuplicateRemoval(library, Path(root), tolerance) if root else None
        self.worker = None
        self.setWindowTitle("중복 후보 비교")
        self.resize(1150, 700)
        layout = QVBoxLayout(self)
        intro = QLabel("제목·아티스트·버전·길이로 찾은 후보입니다. 직접 듣고 판단하세요. 추천은 음질 보증이 아닙니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        body = QHBoxLayout()
        self.group_list = QListWidget()
        self.group_list.setMaximumWidth(280)
        self.group_list.setWordWrap(True)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["유지", "추천 근거", "길이", "음질", "제목", "현재 파일"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.currentCellChanged.connect(self.switch_file)
        body.addWidget(self.group_list)
        body.addWidget(self.table, 1)
        layout.addLayout(body, 1)
        self.player = Player()
        layout.addWidget(self.player)
        actions = QHBoxLayout()
        self.action_buttons = []
        for title, callback in (("선택한 파일 유지 · DB에 기록", self.keep), ("서로 다른 녹음", self.distinct), ("판단 보류 · 닫기", self.reject)):
            button = QPushButton(title)
            button.setProperty('primary', callback == self.keep)
            button.clicked.connect(callback)
            actions.addWidget(button)
            self.action_buttons.append(button)
        self.delete_button = QPushButton('선택 파일 삭제 · 휴지통')
        self.delete_button.setProperty('danger', True)
        self.delete_button.setToolTip('선택한 행을 휴지통으로 이동합니다. 유지 체크된 파일과 후보 전체 삭제는 보류합니다.')
        self.delete_button.clicked.connect(self.delete_selected)
        self.table.itemSelectionChanged.connect(self.update_delete_state)
        actions.addWidget(self.delete_button)
        self.action_buttons.append(self.delete_button)
        layout.addLayout(actions)
        self.status = QLabel('유지할 파일은 체크하세요. 삭제할 파일은 행을 선택하세요(Ctrl/Shift로 여러 개).\n유지 기록의 이동은 파일 정리 미리보기에서 적용합니다. 삭제 복원은 Windows 휴지통 → 폴더 스캔입니다.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.group_list.currentRowChanged.connect(self.show_group)
        self.reload()

    def reload(self):
        self.groups = self.library.duplicate_groups(self.tolerance)
        self.group_list.clear()
        for group in self.groups:
            first = group["tracks"][0]
            self.group_list.addItem(f"{first['artist']}\n{first['title']} · {len(group['tracks'])}개")
        if self.groups:
            self.group_list.setCurrentRow(0)
        else:
            self.table.setRowCount(0)
            self.group_list.addItem("검토할 후보 없음")
        self.update_delete_state()

    def show_group(self, index):
        self.player.stop()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if not 0 <= index < len(self.groups):
            self.table.blockSignals(False)
            return
        tracks = self.groups[index]["tracks"]
        recommended = max(tracks, key=lambda t: (t["grade"] == "A", t["bitrate"] or 0, t["sample_rate"] or 0, bool(t["album"])))
        self.table.setRowCount(len(tracks))
        for i, track in enumerate(tracks):
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, check)
            identical = all(t["hash"] == track["hash"] for t in tracks)
            values = ["추천 · 태그/음질" if track["id"] == recommended["id"] else "",
                      f"{track['duration']:.1f}초", f"{(track['bitrate'] or 0)//1000}k / {track['sample_rate']}Hz",
                      track["title"] + (" · 동일 바이트" if identical else ""), track["path"]]
            for column, value in enumerate(values, 1):
                if column == 5:
                    path = Path(track["path"])
                    value = f"{path.parent.name} / {path.name}"
                item = QTableWidgetItem(value)
                item.setToolTip(track["path"] if column == 5 else value)
                self.table.setItem(i, column, item)
        for column, width in enumerate((50, 150, 90, 155, 220, 270)):
            self.table.setColumnWidth(column, width)
        self.table.blockSignals(False)
        self.update_delete_state()

    def update_delete_state(self):
        self.delete_button.setEnabled(self.removal is not None and self.worker is None
                                      and bool(self.table.selectionModel().selectedRows()))

    def run_removal_task(self, action, callback):
        self.player.stop()
        self.worker = ReviewWorker(lambda *_: action(), self)
        worker = self.worker
        outcome = {}
        worker.result.connect(lambda result: outcome.update(result=result))
        worker.error.connect(lambda error: outcome.update(error=error))
        for control in [self.group_list, self.table, self.player, *self.action_buttons]:
            control.setEnabled(False)

        def finished():
            self.worker = None
            for control in [self.group_list, self.table, self.player, *self.action_buttons]:
                control.setEnabled(True)
            self.update_delete_state()
            worker.deleteLater()
            if 'error' in outcome:
                self.status.setText(outcome['error'])
                QMessageBox.warning(self, '삭제 보류', outcome['error'])
                self.reload()
            else:
                callback(outcome['result'])

        worker.finished.connect(finished)
        worker.start()

    def delete_selected(self):
        index = self.group_list.currentRow()
        if self.worker is not None or self.removal is None or not 0 <= index < len(self.groups):
            return
        rows = sorted(item.row() for item in self.table.selectionModel().selectedRows())
        if not rows:
            return
        if any(self.table.item(row, 0).checkState() == Qt.CheckState.Checked for row in rows):
            QMessageBox.warning(self, '삭제 보류', '유지 체크된 파일이 포함되어 있습니다. 삭제 대상 선택을 다시 확인하세요.')
            return
        group = self.groups[index]
        selected = [group['tracks'][row]['id'] for row in rows]
        self.status.setText('후보 파일 내용·진행 작업 확인 중…')
        self.run_removal_task(lambda: self.removal.preview(group, selected), self.confirm_removal)

    def confirm_removal(self, plan):
        targets = [item for item in plan['candidates'] if item['id'] in plan['selected']]
        box = QMessageBox(QMessageBox.Icon.Warning, '선택 파일 삭제 확인',
                          f"선택한 {len(targets)}개 파일을 Windows 휴지통으로 이동할까요?\n"
                          f"남길 후보: {len(plan['candidates']) - len(targets)}개\n"
                          '영구 삭제하지 않습니다. 복원은 Windows 휴지통에서 한 뒤 폴더를 재스캔하세요.\n\n'
                          + '\n'.join(Path(item['path']).name for item in targets[:8]), parent=self)
        box.setDetailedText('\n'.join(item['path'] for item in targets))
        remove = box.addButton('휴지통으로 이동', QMessageBox.ButtonRole.DestructiveRole)
        remove.setProperty('danger', True)
        cancel = box.addButton('취소', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() != remove:
            self.status.setText('삭제 취소 · 파일을 변경하지 않았습니다.')
            return
        self.status.setText('선택 파일을 휴지통으로 이동 중…')
        self.run_removal_task(lambda: self.removal.apply(plan), self.removal_finished)

    def removal_finished(self, result):
        self.reload()
        self.status.setText(f"휴지통 이동 {result['processed']} · 보류/확인 필요 {result['failed']}\n"
                            '작업 이력에 기록했습니다. 복원은 Windows 휴지통 → 폴더 스캔입니다.')
        if result['failed']:
            QMessageBox.warning(self, '일부 파일 삭제 보류', self.status.text() + '\n' + '\n'.join(
                f"{Path(item['path']).name}: {item.get('reason', '')}" for item in result['targets'] if item['state'] != 'recycled'))

    def switch_file(self, row, *_):
        index = self.group_list.currentRow()
        if 0 <= index < len(self.groups) and 0 <= row < len(self.groups[index]["tracks"]):
            self.player.set_track(self.groups[index]["tracks"][row]["path"], retain_position=True)

    def keep(self):
        index = self.group_list.currentRow()
        if not 0 <= index < len(self.groups):
            return
        group = self.groups[index]
        kept = [track["id"] for i, track in enumerate(group["tracks"]) if self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        self.record(group, "keep", kept)

    def distinct(self):
        index = self.group_list.currentRow()
        if 0 <= index < len(self.groups):
            group = self.groups[index]
            self.record(group, "distinct", [track["id"] for track in group["tracks"]])

    def record(self, group, decision, kept):
        try:
            self.library.decide_duplicates(group["signature"], decision, kept)
            self.reload()
        except ValueError as error:
            QMessageBox.warning(self, "검토 보류", str(error))

    def done(self, result):
        if self.worker is not None:
            self.status.setText('파일 확인·이동이 끝난 뒤 창을 닫을 수 있습니다.')
            return
        self.player.stop()
        super().done(result)

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText('파일 확인·이동이 끝난 뒤 창을 닫을 수 있습니다.')
            event.ignore()
            return
        super().closeEvent(event)
