"""Read-only originals, copy-based local integration and render verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QLoggingCategory, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from music_sorter.database import Library
from music_sorter.scanner import scan_library
from music_sorter.settings import CredentialStore, Settings
from music_sorter.ui.main_window import MainWindow
from music_sorter.ui.settings_dialog import SettingsDialog
from music_sorter.ui.theme import apply_theme


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=128)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("검증 출력은 새 디렉터리를 지정하세요.")
    candidates = sorted(args.source.rglob("*.mp3"))[:args.count]
    if not candidates:
        raise RuntimeError("MP3 샘플이 없습니다.")
    sample_root = (args.output / "sample-library").resolve()
    sample_root.mkdir(parents=True)
    originals = {}
    for index, source in enumerate(candidates):
        originals[source] = digest(source)
        target = sample_root / f"{index:04d}" / source.name
        target.parent.mkdir()
        shutil.copy2(source, target)
        assert digest(target) == originals[source]
    app = QApplication([])
    QLoggingCategory.setFilterRules("qt.multimedia.ffmpeg.*=false")
    library = Library(args.output / "user-data" / "music-sorter.sqlite3")
    start = time.perf_counter()
    first_scan = scan_library(library, sample_root)
    seconds = time.perf_counter() - start
    tracks = library.list_tracks(limit=500)[0]
    assert tracks and not first_scan["failed"]
    selected = tracks[0]
    before = digest(Path(selected["path"]))
    library.save_manual(selected["id"], {"major": "가요", "concept": []}, selected["revision"])
    second_scan = scan_library(library, sample_root)
    assert library.track(selected["id"])["classification"]["concept"]["protected"]
    assert digest(Path(selected["path"])) == before
    library.backup(args.output / "backup.sqlite3")
    settings = Settings(music_root=str(sample_root))
    config = args.output / "user-data" / "settings.json"
    settings.save(config)
    assert Settings.load(config) == settings
    vault = CredentialStore()
    test_profile = "verification-" + args.output.name
    synthetic_value = "synthetic-local-verification-value"
    vault.set(test_profile, synthetic_value)
    try:
        assert vault.get(test_profile) == synthetic_value
    finally:
        vault.delete(test_profile)
    assert vault.get(test_profile) is None
    window = MainWindow(library, settings, config)
    apply_theme(app, "light", 1)
    window.show()
    results = dict(sample_count=len(candidates), first_scan=first_scan, second_scan=second_scan,
                   first_scan_seconds=round(seconds, 3), duplicate_groups=len(library.duplicate_groups()),
                   manual_protection_survived=True, originals_unchanged=False,
                   windows_credential_store_roundtrip=True, media_loaded=False, media_position_advanced=False)
    player = window.player.player
    window.player.audio.setVolume(0)  # Automated playback verification must not disturb the user's audio.
    player.setSource(QUrl.fromLocalFile(selected["path"]))
    player.play()

    def capture():
        results["media_loaded"] = player.duration() > 0
        results["media_position_advanced"] = player.position() > 0
        window.grab().save(str(args.output / "library-light.png"))
        apply_theme(app, "dark", 1)
        app.processEvents()
        window.grab().save(str(args.output / "library-dark.png"))
        dialog = SettingsDialog(settings, config, library, window)
        dialog.menu.setCurrentRow(2)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(args.output / "settings-api.png"))
        dialog.close()
        results["originals_unchanged"] = all(digest(path) == expected for path, expected in originals.items())
        assert results["originals_unchanged"]
        (args.output / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(results, ensure_ascii=True))
        window.player.stop()
        app.quit()

    QTimer.singleShot(2000, capture)
    app.exec()


if __name__ == "__main__":
    main()
