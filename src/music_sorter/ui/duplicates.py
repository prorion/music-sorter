from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .player import Player


class DuplicateDialog(QDialog):
    def __init__(self, library, tolerance, parent=None):
        super().__init__(parent)
        self.library, self.tolerance = library, tolerance
        self.setWindowTitle("중복 후보 비교 · 파일 이동 없이 검토")
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
        for title, callback in (("선택한 파일 유지 · DB에 기록", self.keep), ("서로 다른 녹음", self.distinct), ("판단 보류 · 닫기", self.reject)):
            button = QPushButton(title)
            button.setProperty('primary', callback == self.keep)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        layout.addWidget(QLabel("여러 파일을 체크할 수 있습니다. 선택 결과의 이동은 파일 정리 미리보기에서 검토 후 적용합니다."))
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
        self.player.stop()
        super().done(result)
