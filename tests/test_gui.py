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
