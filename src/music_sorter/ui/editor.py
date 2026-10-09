from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..classification import AXES, LABELS, TAXONOMY
from ..catalog import options as catalog_options
from .wording import SOURCES, readable


class Choices(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)
        self.unknown = QCheckBox("확인 필요")
        self.unknown.setChecked(True)
        self.none = QCheckBox("확인했지만 해당되는 분류 없음")
        self.none.setVisible(False)
        self.list = QListWidget()
        self.list.setMaximumHeight(88)
        layout.addWidget(self.unknown)
        layout.addWidget(self.none)
        layout.addWidget(self.list)
        self.list.itemChanged.connect(lambda *_: self.changed.emit())
        self.unknown.toggled.connect(self.toggle_unknown)
        self.none.toggled.connect(self.toggle_none)

    def toggle_unknown(self, checked):
        self.list.setEnabled(not checked and not self.none.isChecked())
        self.none.setEnabled(not checked)
        self.changed.emit()

    def toggle_none(self, checked):
        self.list.setEnabled(not self.unknown.isChecked() and not checked)
        self.changed.emit()

    def populate(self, options, value, concept=False):
        self.concept = concept
        self.list.blockSignals(True)
        self.list.clear()
        for option in options:
            item = QListWidgetItem(option)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if value and option in value else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.none.setVisible(concept)
        self.none.setChecked(concept and value == [])
        self.unknown.setChecked(value is None)
        self.toggle_unknown(value is None)

    def value(self):
        if self.unknown.isChecked():
            return None
        if getattr(self, "concept", False) and self.none.isChecked():
            return []
        return [self.list.item(i).text() for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.CheckState.Checked]


class TrackEditor(QWidget):
    saved = Signal()
    external_review = Signal(str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.setObjectName("trackEditor")
        self.library, self.current = library, None
        self.loading = True
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        heading = QLabel("곡 상세")
        heading.setObjectName("muted")
        layout.addWidget(heading)
        self.title = QLabel("곡을 선택하세요")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.title)
        self.metadata = QLabel("분류는 앱에 저장합니다. 음악 파일은 변경하지 않습니다.")
        self.metadata.setWordWrap(True)
        self.metadata.setObjectName("subtle")
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.metadata)
        self.fields, self.edits, self.protection, self.unlock_buttons = {}, {}, {}, {}
        for axis in AXES:
            row = QHBoxLayout()
            edit = QCheckBox(LABELS[axis] + " 수정")
            row.addWidget(edit)
            protection = QLabel()
            row.addStretch()
            unlock = QPushButton("자동 수정 허용")
            unlock.setObjectName("ghost")
            unlock.setToolTip("이 항목을 다음 AI 분류에서 수정할 수 있게 합니다. 지금 분류를 실행하지는 않습니다.")
            unlock.clicked.connect(lambda _, a=axis: self.unlock(a))
            self.unlock_buttons[axis] = unlock
            row.addWidget(unlock)
            layout.addLayout(row)
            layout.addWidget(protection)
            if axis in {"major", "vocal"}:
                field = QComboBox()
                field.addItem("확인 필요", None)
                for option in TAXONOMY[axis]:
                    field.addItem(option, option)
                field.currentIndexChanged.connect(lambda _, a=axis: self.edited(a))
            else:
                field = Choices()
                field.changed.connect(lambda a=axis: self.edited(a))
            layout.addWidget(field)
            self.fields[axis], self.edits[axis], self.protection[axis] = field, edit, protection
        self.hint = QLabel("바꿀 항목을 선택하고 저장하세요. 해당되는 컨셉이 없으면 「해당되는 분류 없음」을 선택하세요.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.save_button = QPushButton("분류 저장")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.review_button = QPushButton("앱 밖에서 바뀐 파일 확인")
        self.review_button.clicked.connect(lambda: self.external_review.emit(self.current["id"]) if self.current else None)
        layout.addWidget(self.review_button)
        self.history_button = QPushButton('분류 변경 기록·되돌리기')
        self.history_button.clicked.connect(self.open_history)
        layout.addWidget(self.history_button)
        layout.addStretch()
        self.fields["major"].currentIndexChanged.connect(self.major_changed)
        self.loading = False
        self.setEnabled(False)

    def edited(self, axis):
        if not self.loading:
            self.edits[axis].setChecked(True)

    def major_changed(self):
        if self.loading:
            return
        major = self.fields["major"].currentData()
        options = catalog_options('subgenre', major)
        previous = self.fields["subgenre"].value()
        incompatible = previous is not None and any(value not in options for value in previous)
        self.loading = True
        self.fields["subgenre"].populate(options, None if incompatible else previous)
        self.loading = False
        if incompatible:
            self.edits["subgenre"].setChecked(True)
            self.hint.setText("새 대분류에 맞지 않는 세부 장르는 다시 선택해야 합니다. 새 장르를 선택하거나 「확인 필요」로 저장하세요.")

    def load_track(self, track):
        self.loading = True
        self.current = track
        self.setEnabled(True)
        self.title.setText(f"{track['title']}\n{track['artist'] or '아티스트 정보 없음'}")
        rate = f"{(track['bitrate'] or 0) // 1000}kbps · {track['sample_rate'] or '?'}Hz"
        state = {"ready": "확인됨", "missing": "누락", "external_change": "외부 변경", "link_pending": "연결 보류", "unavailable": "확인 불가", "replaced": "교체된 기록"}.get(track["file_state"], "확인 필요")
        self.metadata.setText(f"{rate} · {state}\n기존 장르: {track['metadata_json'].get('genre') or '없음'}")
        self.metadata.setToolTip(track['path'])
        major = track["classification"]["major"]["value"]
        for axis in AXES:
            data = track["classification"][axis]
            field = self.fields[axis]
            if isinstance(field, QComboBox):
                field.setCurrentIndex(max(0, field.findData(data["value"])))
            else:
                options = catalog_options(axis, major, data['value'])
                field.populate(options, data["value"], axis == "concept")
            self.edits[axis].setChecked(False)
            status = {"unclassified": "미분류", "unresolved": "확인 필요", "confirmed": "확정"}[data["status"]]
            self.protection[axis].setText(f"{'🔒 직접 수정한 분류' if data['protected'] else 'AI 수정 가능'} · {status}")
            self.protection[axis].setToolTip(f"분류한 방법: {SOURCES.get(data['source'], data['source'] or '없음')}\n이유: {readable(data['reason'])}")
            self.unlock_buttons[axis].setVisible(bool(data['protected']))
        self.save_button.setEnabled(track["file_state"] == "ready")
        self.review_button.setText("이전 곡 기록과 비교" if track["file_state"] == "link_pending" else "앱 밖에서 바뀐 파일 확인")
        self.review_button.setVisible(track["file_state"] in {"external_change", "link_pending"})
        self.hint.setText("선택한 항목의 분류만 저장합니다. 음악 파일은 변경하지 않습니다.")
        self.loading = False

    def dirty(self):
        return any(edit.isChecked() for edit in self.edits.values())

    def open_history(self):
        if not self.current:
            return
        if self.dirty():
            QMessageBox.information(self, '수정 중', '수정한 내용을 먼저 저장하거나 취소하세요.')
            return
        from .history_dialog import HistoryDialog
        dialog = HistoryDialog(self.library, self.current['id'], self)
        dialog.exec()
        if getattr(dialog, 'changed', False):
            self.load_track(self.library.track(self.current['id']))
            self.saved.emit()

    def save(self):
        if not self.current or not self.dirty():
            return
        changes = {axis: field.currentData() if isinstance(field, QComboBox) else field.value()
                   for axis, field in self.fields.items() if self.edits[axis].isChecked()}
        try:
            self.library.save_manual(self.current["id"], changes, self.current["revision"])
            self.load_track(self.library.track(self.current["id"]))
            self.saved.emit()
        except ValueError as error:
            QMessageBox.warning(self, "저장 보류", str(error))

    def unlock(self, axis):
        if not self.current:
            return
        if self.dirty():
            QMessageBox.information(self, "수정 중", "수정한 내용을 먼저 저장하거나 취소하세요.")
            return
        self.library.unlock(self.current["id"], axis)
        self.load_track(self.library.track(self.current["id"]))
        self.saved.emit()
