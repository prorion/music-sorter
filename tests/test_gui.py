from PySide6.QtWidgets import QMessageBox

from music_sorter.scanner import scan_library
from music_sorter.settings import Settings
from music_sorter.ui.main_window import MainWindow
from music_sorter.ui.settings_dialog import SettingsDialog


def test_gui_manual_save_isolated_to_changed_axes(qtbot, library, root, song, fake_reader, tmp_path):
    scan_library(library, root)
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / "settings.json")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.editor.current is not None)
    original = window.editor.current
    combo = window.editor.fields["major"]
    combo.setCurrentIndex(combo.findData("가요"))
    window.editor.save()
    updated = library.track(original["id"])
    assert updated["classification"]["major"]["protected"]
    assert updated["classification"]["mood"]["status"] == "unclassified"
    assert song.read_bytes() == b"original-audio"
    assert not window.editor.dirty()


def test_settings_cancel_and_apply_never_write_keys(qtbot, library, tmp_path, monkeypatch):
    monkeypatch.setattr("music_sorter.settings.CredentialStore.get", lambda *_: None)
    def fail_warning(parent, title, message):
        raise AssertionError(message)
    monkeypatch.setattr("music_sorter.ui.settings_dialog.QMessageBox.warning", fail_warning)
    monkeypatch.setattr("music_sorter.ui.settings_dialog.QMessageBox.question", lambda *_: QMessageBox.StandardButton.Yes)
    path = tmp_path / "settings.json"
    settings = Settings()
    dialog = SettingsDialog(settings, path, library)
    qtbot.addWidget(dialog)
    assert dialog.menu.count() == 7
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    dialog.key_edits["openai"].setText("not-an-api-key-test")
    dialog.reject()
    assert not path.exists()
    dialog.key_edits["openai"].setText("not-an-api-key-test")
    assert dialog.save()
    assert Settings.load(path).theme == "dark"
    assert "not-an-api-key-test" not in path.read_text("utf-8")
    dialog.key_edits["openai"].clear()


def test_threaded_scan_refreshes_table(qtbot, library, root, song, fake_reader, tmp_path):
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / "settings.json")
    qtbot.addWidget(window)
    window.start_scan()
    qtbot.waitUntil(lambda: window.worker is not None and not window.worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    assert "스캔 완료" in window.status.text()


def test_settings_fields_grow_spin_arrows_work_and_apply_tracks_saved_changes(qtbot, qapp, library, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox
    from music_sorter.ui.theme import apply_theme
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: None)
    apply_theme(qapp, 'dark', 1)
    dialog = SettingsDialog(Settings(), tmp_path / 'settings.json', library)
    qtbot.addWidget(dialog)
    dialog.menu.setCurrentRow(3)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert not dialog.apply_button.isEnabled()
    assert all(control.width() >= 250 for control in dialog.models)
    spin = dialog.advanced['llm_tracks_per_request']
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    down = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, spin)
    assert down.width() >= 20 and down.height() >= 18
    qtbot.mouseClick(spin, Qt.MouseButton.LeftButton, pos=down.center())
    assert spin.value() == 19 and dialog.apply_button.isEnabled()
    qtbot.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible() and not dialog.apply_button.isEnabled()
    assert Settings.load(tmp_path / 'settings.json').llm_tracks_per_request == 19
    spin.setValue(18)
    assert dialog.apply_button.isEnabled()
    spin.setValue(19)
    assert not dialog.apply_button.isEnabled()


def test_settings_actual_db_path_is_readonly_copyable_and_opens_containing_folder(qtbot, qapp, library, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: None)
    opened = []
    monkeypatch.setattr('music_sorter.ui.settings_dialog.QDesktopServices.openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    dialog = SettingsDialog(Settings(), tmp_path / 'settings.json', library)
    qtbot.addWidget(dialog)
    dialog.menu.setCurrentRow(1)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.db_path.isReadOnly() and dialog.db_path.isEnabled()
    assert dialog.db_path.text() == str(library.path.resolve())
    buttons = {button.accessibleName(): button for button in dialog.findChildren(QPushButton)}
    old_clipboard = qapp.clipboard().text()
    try:
        qtbot.mouseClick(buttons['DB 파일 경로 복사'], Qt.MouseButton.LeftButton)
        assert qapp.clipboard().text() == str(library.path.resolve())
    finally:
        qapp.clipboard().setText(old_clipboard)
    qtbot.mouseClick(buttons['DB 파일 폴더 열기'], Qt.MouseButton.LeftButton)
    from pathlib import Path
    assert [Path(path) for path in opened] == [library.path.parent.resolve()]
    assert not dialog.apply_button.isEnabled()


def test_duplicate_delete_selection_guard_and_confirmation_cancel(qtbot, library, root, song, fake_reader, tmp_path, monkeypatch):
    import shutil
    from music_sorter.scanner import scan_library
    from music_sorter.ui.duplicates import DuplicateDialog
    from PySide6.QtCore import Qt
    copy = root / 'copy' / song.name
    copy.parent.mkdir()
    shutil.copy2(song, copy)
    scan_library(library, root)
    dialog = DuplicateDialog(library, 3, root=root)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.delete_button.isEnabled()
    dialog.table.selectRow(0)
    assert dialog.delete_button.isEnabled()
    warnings = []
    monkeypatch.setattr('music_sorter.ui.duplicates.QMessageBox.warning', lambda *args: warnings.append(args[2]))
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog.delete_selected()
    assert warnings and dialog.worker is None
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    confirmations = []
    def cancel(box):
        confirmations.append((box.defaultButton().text(), box.detailedText()))
        return 0
    monkeypatch.setattr('music_sorter.ui.duplicates.QMessageBox.exec', cancel)
    dialog.delete_selected()
    qtbot.waitUntil(lambda: dialog.worker is None and bool(confirmations), timeout=10000)
    assert confirmations[0][0] == '취소'
    assert dialog.groups[0]['tracks'][0]['path'] in confirmations[0][1]
    assert song.exists() and copy.exists()
    assert all(job['kind'] != 'duplicate_delete' for job in library.jobs())


def test_duplicate_confirmed_delete_refreshes_table_and_keeps_checked_survivor(qtbot, library, root, song, fake_reader, tmp_path, monkeypatch):
    import shutil
    from music_sorter.scanner import scan_library
    from music_sorter.ui.duplicates import DuplicateDialog
    from PySide6.QtCore import Qt
    from pathlib import Path
    copy = root / 'copy' / song.name
    copy.parent.mkdir()
    shutil.copy2(song, copy)
    scan_library(library, root)
    dialog = DuplicateDialog(library, 3, root=root)
    qtbot.addWidget(dialog)
    dialog.show()
    target, survivor = dialog.groups[0]['tracks']
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    dialog.table.selectRow(0)
    def recycle(path):
        destination = tmp_path / 'fake-recycle.mp3'
        shutil.move(path, destination)
        return str(destination)
    dialog.removal.recycle = recycle
    monkeypatch.setattr('music_sorter.ui.duplicates.QMessageBox.exec', lambda *_: 0)
    monkeypatch.setattr('music_sorter.ui.duplicates.QMessageBox.clickedButton',
                        lambda box: next(button for button in box.buttons() if button.text() == '휴지통으로 이동'))
    dialog.delete_selected()
    qtbot.waitUntil(lambda: dialog.worker is None and '휴지통 이동 1' in dialog.status.text(), timeout=10000)
    assert not Path(target['path']).exists() and Path(survivor['path']).exists()
    assert dialog.table.rowCount() == 0 and not dialog.delete_button.isEnabled()
    assert library.track(target['id'])['file_state'] == 'missing'


def test_standard_confirmation_has_distinct_actions_without_changing_safe_default(qtbot, qapp):
    from music_sorter.ui.theme import apply_theme
    apply_theme(qapp, 'light', 1)
    dialog = QMessageBox(QMessageBox.Icon.Question, '확인', '변경할까요?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.button(QMessageBox.StandardButton.Yes).property('primary')
    assert not dialog.button(QMessageBox.StandardButton.No).property('primary')
    assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)


def test_rejected_navigation_restores_filter_and_keeps_draft(qtbot, library, root, song, fake_reader, tmp_path, monkeypatch):
    scan_library(library, root)
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / "settings.json")
    qtbot.addWidget(window)
    window.editor.fields["major"].setCurrentIndex(1)
    monkeypatch.setattr("music_sorter.ui.main_window.QMessageBox.question", lambda *_: QMessageBox.StandardButton.No)
    window.search.setText("not-present")
    window.search_timer.stop()
    window.filters_changed()
    assert window.search.text() == "" and window.editor.dirty()
    window.navigation.setCurrentRow(4)
    assert window.navigation.currentRow() == 0
    monkeypatch.setattr("music_sorter.ui.main_window.QMessageBox.question", lambda *_: QMessageBox.StandardButton.Yes)
    assert window.discard_edits()
    assert not window.editor.dirty()


def test_bulk_dialog_previews_and_applies_only_selected_rows(qtbot, library, root, song, fake_reader, monkeypatch):
    from music_sorter.ui.bulk import BulkDialog
    (root / "두번째.mp3").write_bytes(b"second")
    scan_library(library, root)
    rows = library.list_tracks()[0]
    dialog = BulkDialog(library, {}, [rows[0]["id"]])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.edits["concept"].setChecked(True)
    dialog.modes["concept"].setCurrentIndex(dialog.modes["concept"].findData("none"))
    dialog.start_preview()
    qtbot.waitUntil(lambda: dialog.worker is not None and not dialog.worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: dialog.apply_button.isEnabled())
    assert dialog.preview["total"] == 1 and dialog.model.rowCount() == 1
    monkeypatch.setattr("music_sorter.ui.bulk.QMessageBox.question", lambda *_: QMessageBox.StandardButton.Yes)
    dialog.start_apply()
    qtbot.waitUntil(lambda: not dialog.worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: hasattr(dialog, "applied"))
    assert library.track(rows[0]["id"])["classification"]["concept"]["protected"]
    assert not library.track(rows[1]["id"])["classification"]["concept"]["protected"]
    assert not dialog.apply_button.isEnabled()
    dialog.scope.setCurrentIndex(2)
    assert dialog.preview is None


def test_link_dialog_has_no_default_and_saves_after_worker_finishes(qtbot, library, root, song, fake_reader, monkeypatch):
    from test_review import pending_pair
    from music_sorter.ui.links import LinkDialog
    original, pending = pending_pair(library, root, song)
    dialog = LinkDialog(library, pending["id"])
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table.currentRow() == -1
    warnings = []
    monkeypatch.setattr("music_sorter.ui.links.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    dialog.save()
    assert warnings and dialog.worker is None
    dialog.table.setCurrentCell(0, 0)
    dialog.mode.setCurrentIndex(dialog.mode.findData("link"))
    dialog.inherit.setCurrentIndex(dialog.inherit.findData(True))
    dialog.save()
    qtbot.waitUntil(lambda: not dialog.worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: hasattr(dialog, "result_id"))
    assert dialog.result_id == original["id"]
    assert library.track(original["id"])["classification"]["major"]["protected"]


def test_mood_concept_filters_and_rejected_filter_keep_draft(qtbot, library, root, song, fake_reader, tmp_path, monkeypatch):
    scan_library(library, root)
    row = library.list_tracks()[0][0]
    library.save_manual(row["id"], {"mood": ["잔잔한"], "concept": ["카페"]}, row["revision"])
    window = MainWindow(library, Settings(music_root=str(root)), tmp_path / "settings.json")
    qtbot.addWidget(window)
    window.mood_filter.setCurrentIndex(window.mood_filter.findData("잔잔한"))
    window.concept_filter.setCurrentIndex(window.concept_filter.findData("카페"))
    assert window.model.rowCount() == 1
    window.editor.fields["major"].setCurrentIndex(1)
    monkeypatch.setattr("music_sorter.ui.main_window.QMessageBox.question", lambda *_: QMessageBox.StandardButton.No)
    window.concept_filter.setCurrentIndex(window.concept_filter.findData("운동"))
    assert window.concept_filter.currentData() == "카페" and window.editor.dirty()
    monkeypatch.setattr("music_sorter.ui.main_window.QMessageBox.question", lambda *_: QMessageBox.StandardButton.Yes)
    window.discard_edits()


def test_classification_plan_requires_budget_has_no_generation_and_locks_options(qtbot, library, root, song, fake_reader):
    from music_sorter.ui.classify_dialog import ClassifyDialog
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    dialog = ClassifyDialog(library, Settings(music_root=str(root)), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.budget.setText('1')
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert dialog.job_id and dialog.engine.summary(dialog.job_id)['counts'] == {'prepared': 1}
    assert not dialog.purpose.isEnabled() and dialog.budget.isEnabled() and dialog.run_button.isEnabled()
    assert dialog.engine.summary(dialog.job_id)['actual'] == 0
    assert not dialog.collect_button.isEnabled()


def test_external_missing_credentials_skips_and_does_not_classify(qtbot, library, root, song, fake_reader, monkeypatch):
    from music_sorter.ui.external_dialog import ExternalDialog
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    monkeypatch.setattr('music_sorter.ui.external_dialog.CredentialStore.get', lambda *_: None)
    dialog = ExternalDialog(library, Settings(music_root=str(root)), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=10000)
    assert dialog.table.rowCount() == 2 and dialog.start_button.isEnabled()
    assert library.track(track['id'])['review_state'] == 'unclassified'
