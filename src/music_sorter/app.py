from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QLoggingCategory, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .database import Library
from .settings import Settings, data_directory
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="music-sorter Windows GUI")
    parser.add_argument("--data-dir", type=Path, default=data_directory(), help="별도 로컬 DB·설정 디렉터리")
    parser.add_argument("--smoke-screen", type=Path, help="GUI 렌더 검증용 PNG 저장 후 종료")
    parser.add_argument("--smoke-media", type=Path, help="렌더 검증과 함께 복사본 MP3를 음소거 재생")
    args = parser.parse_args(argv)
    if args.smoke_media and not args.smoke_screen:
        parser.error("--smoke-media는 --smoke-screen과 함께 사용하세요.")
    app = QApplication(sys.argv[:1])
    QLoggingCategory.setFilterRules("qt.multimedia.ffmpeg.*=false")
    app.setApplicationName("music-sorter")
    app.setOrganizationName("music-sorter")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(args.data_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(None, "이미 실행 중", "이 데이터 디렉터리는 다른 music-sorter가 사용 중입니다.")
        return 1
    try:
        config_path = args.data_dir / "settings.json"
        settings = Settings.load(config_path)
        library = Library(args.data_dir / "music-sorter.sqlite3")
        library.recover_interrupted_jobs()
        apply_theme(app, settings.theme, settings.font_scale)
        window = MainWindow(library, settings, config_path)
        window.show()
        if args.smoke_media:
            window.player.audio.setVolume(0)
            window.player.set_track(str(args.smoke_media.resolve()))
            window.player.player.play()
        if args.smoke_screen:
            def snapshot():
                args.smoke_screen.parent.mkdir(parents=True, exist_ok=True)
                media_loaded = window.player.player.duration() > 0
                advanced = window.player.player.position() > 0
                report = dict(rendered=True, device_pixel_ratio=window.devicePixelRatioF(),
                              media_requested=bool(args.smoke_media), media_loaded=media_loaded,
                              media_position_advanced=advanced)
                args.smoke_screen.with_suffix(".json").write_text(json.dumps(report, indent=2), "utf-8")
                if not window.grab().save(str(args.smoke_screen)):
                    app.exit(2)
                elif args.smoke_media and not (media_loaded and advanced):
                    app.exit(3)
                else:
                    window.player.stop()
                    app.quit()
            QTimer.singleShot(2000 if args.smoke_media else 700, snapshot)
        return app.exec()
    except Exception as error:
        QMessageBox.critical(None, "시작 보류", f"로컬 설정·DB를 열지 못했습니다 ({type(error).__name__}). 원본 데이터를 보존한 채 설정·DB 상태를 확인하세요.")
        return 1
    finally:
        lock.unlock()
