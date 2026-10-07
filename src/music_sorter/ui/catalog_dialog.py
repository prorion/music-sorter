import json
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from ..catalog import change_catalog, load_catalog


class CatalogDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library, self.path = library, library.path.parent / 'taxonomy.json'
        self.setWindowTitle('분류 태그 목록·제안 승인')
        self.resize(820, 650)
        layout = QVBoxLayout(self)
        note = QLabel('추가·사용 중단은 목록 버전과 이력으로 보존합니다. 기존 판정·음악 파일을 바꾸지 않습니다.\n새 목록은 앱 재시작 후 새 작업에 사용합니다. 진행 중인 요청은 시작 당시 목록을 유지합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.group = QComboBox()
        for major in load_catalog(self.path)['major']:
            self.group.addItem('세부 장르 · ' + major, 'subgenre:' + major)
        for title, group in [('분위기', 'mood'), ('컨셉', 'concept')]:
            self.group.addItem(title, group)
        self.group.currentIndexChanged.connect(self.reload)
        layout.addWidget(self.group)
        self.tags = QListWidget()
        layout.addWidget(self.tags, 1)
        self.name = QLineEdit()
        self.name.setPlaceholderText('정식 목록에 추가할 태그 이름')
        layout.addWidget(self.name)
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(130)
        self.suggestions.itemClicked.connect(lambda item: self.name.setText(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(QLabel('모델 제안 빈도 · 클릭하면 이름 입력 · 추가할 종류는 위에서 선택'))
        layout.addWidget(self.suggestions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        for title, action in [('입력 태그 추가·제안 승인', 'add'), ('선택 태그 사용 중단', 'retire'), ('선택 태그 다시 사용', 'reactivate')]:
            button = QPushButton(title)
            button.clicked.connect(lambda _, mode=action: self.change(mode))
            buttons.addWidget(button)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        with library.connection() as db:
            counts = db.execute("SELECT j.value,count(*) FROM llm_targets t,json_each(t.result,'$.suggested_tags') j WHERE t.result IS NOT NULL AND j.type='text' GROUP BY j.value ORDER BY count(*) DESC,j.value LIMIT 200").fetchall()
        for name, count in counts:
            item = QListWidgetItem(f'{name} · {count:,}건')
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.suggestions.addItem(item)
        self.reload()

    def reload(self):
        self.catalog = load_catalog(self.path)
        group = self.group.currentData()
        values = self.catalog['major'][group.removeprefix('subgenre:')] if group.startswith('subgenre:') else self.catalog[group]
        retired = self.catalog.get('retired', {}).get(group, [])
        self.tags.clear()
        for name in values:
            item = QListWidgetItem(name + (' · 사용 중단' if name in retired else ''))
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.tags.addItem(item)
        self.status.setText(f"목록 버전 {self.catalog['version']} · 변경 이력 {len(self.catalog.get('history', [])):,}건")

    def change(self, action):
        item = self.tags.currentItem()
        name = self.name.text().strip() if action == 'add' else item.data(Qt.ItemDataRole.UserRole) if item else ''
        if not name:
            self.status.setText('추가할 이름을 입력하거나 태그를 선택하세요.')
            return
        group = self.group.currentData()
        axis = 'subgenre' if group.startswith('subgenre:') else group
        with self.library.connection() as db:
            affected = db.execute("SELECT count(*) FROM tracks t WHERE EXISTS(SELECT 1 FROM json_each(t.classification,?) j WHERE j.value=?) AND (?!='subgenre' OR json_extract(t.classification,'$.major.value')=?)",
                                  (f'$.{axis}.value', name, axis, group.removeprefix('subgenre:'))).fetchone()[0]
        text = f"{self.group.currentText()} / {name}\n목록 버전 {self.catalog['version']} → {self.catalog['version'] + 1}\n현재 이 태그를 사용하는 곡 {affected:,}개. 기존 판정·보호는 유지합니다.\n재시작 후 새 목록을 사용하며 재분류는 별도 실행합니다. 진행할까요?"
        if QMessageBox.question(self, '목록 변경 확인', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            change_catalog(self.path, group, name, action, self.catalog['version'])
            self.changed = True
            self.name.clear()
            self.reload()
            self.status.setText(f"목록 버전 {self.catalog['version']} 저장 완료. 앱을 다시 열면 새 선택 목록을 사용합니다.")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '목록 변경 보류', str(error))
