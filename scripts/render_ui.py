"""Render real Qt widgets using an existing verification DB (no music writes)."""
import argparse
import json
from pathlib import Path

from PySide6.QtCore import QLoggingCategory, QTimer
from PySide6.QtWidgets import QApplication

from music_sorter.database import Library
from music_sorter.settings import Settings
from music_sorter.ui.duplicates import DuplicateDialog
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
        window.player.stop()
        (args.output / "render.json").write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report))
        app.quit()

    QTimer.singleShot(300, capture)
    app.exec()


if __name__ == "__main__":
    main()
