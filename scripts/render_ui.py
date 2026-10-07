"""Render real Qt widgets using an existing verification DB (no music writes)."""
import argparse
import json
from pathlib import Path

from PySide6.QtCore import QLoggingCategory, QTimer, Qt
from PySide6.QtWidgets import QApplication

from music_sorter.database import Library
from music_sorter.settings import Settings
from music_sorter.ui.duplicates import DuplicateDialog
from music_sorter.ui.bulk import BulkDialog
from music_sorter.ui.links import LinkDialog
from music_sorter.ui.main_window import MainWindow
from music_sorter.ui.settings_dialog import SettingsDialog
from music_sorter.ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    QLoggingCategory.setFilterRules("qt.multimedia.ffmpeg.*=false")
    settings = Settings.load(args.data_dir / "settings.json")
    library = Library(args.data_dir / "music-sorter.sqlite3")
    apply_theme(app, "light", 1)
    window = MainWindow(library, settings, args.data_dir / "settings.json")
    window.show()
    report = {"device_pixel_ratio": window.devicePixelRatioF(), "screens": []}

    def capture():
        for theme in ("light", "dark"):
            apply_theme(app, theme, 1)
            for width, height, label in ((1440, 900, "normal"), (1100, 720, "small")):
                window.resize(width, height)
                app.processEvents()
                name = f"library-{theme}-{label}.png"
                assert window.grab().save(str(args.output / name))
                report["screens"].append(name)
        dialog = SettingsDialog(settings, args.data_dir / "settings.json", library, window)
        dialog.menu.setCurrentRow(2)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(args.output / "settings-api.png"))
        dialog.close()
        duplicates = DuplicateDialog(library, settings.duplicate_tolerance_seconds, window)
        duplicates.show()
        app.processEvents()
        duplicates.grab().save(str(args.output / "duplicates.png"))
        duplicates.close()
        bulk = BulkDialog(library, window.current_filters(), [window.model.rows[0]["id"]] if window.model.rows else [], window)
        bulk.show()
        app.processEvents()
        bulk.grab().save(str(args.output / "bulk-editor.png"))

        def finish():
            if bulk.preview:
                bulk.grab().save(str(args.output / "bulk-preview.png"))
                report["bulk_preview"] = bulk.preview
            bulk.close()
            capture_links()

        def capture_links():
            from music_sorter.ui.classify_dialog import ClassifyDialog
            from music_sorter.ui.external_dialog import ExternalDialog
            classify = ClassifyDialog(library, settings, [window.model.rows[0]['id']] if window.model.rows else [], {}, window)
            classify.budget.setText('1')
            classify.show()
            app.processEvents()
            classify.grab().save(str(args.output / 'classification-plan.png'))
            classify.close()
            external = ExternalDialog(library, settings, [], {}, window)
            external.show()
            app.processEvents()
            external.grab().save(str(args.output / 'external-information.png'))
            external.close()
            pending, _ = library.list_tracks(state="link_pending", limit=1)
            if pending:
                links = LinkDialog(library, pending[0]["id"], window)
                links.show()
                app.processEvents()
                links.grab().save(str(args.output / "link-review.png"))
                links.close()
            window.player.stop()
            (args.output / "render.json").write_text(json.dumps(report, indent=2), "utf-8")
            print(json.dumps(report))
            app.quit()

        if window.model.rows:
            bulk.scope.setCurrentIndex(bulk.scope.findData("all"))
            bulk.edits["mood"].setChecked(True)
            for index in range(bulk.values["mood"].count()):
                item = bulk.values["mood"].item(index)
                if item.text() == "감성적인":
                    item.setCheckState(Qt.CheckState.Checked)
            bulk.start_preview()
            bulk.worker.finished.connect(finish)
        else:
            finish()

    QTimer.singleShot(300, capture)
    app.exec()


if __name__ == "__main__":
    main()
