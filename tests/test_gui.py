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
