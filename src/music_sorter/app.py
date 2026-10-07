from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QLoggingCategory, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .database import Library
from .file_ops import FileOperations
from .settings import Settings, data_directory
from .ui.main_window import MainWindow
from .ui.settings_dialog import SettingsDialog
from .ui.theme import apply_theme


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="music-sorter Windows GUI")
    parser.add_argument("--data-dir", type=Path, help="별도 로컬 DB·설정 디렉터리")
    parser.add_argument('--profile', type=Path, help='명시적 .env 개발 프로필. 이 모드에서만 프로세스 환경 변수를 적용')
    parser.add_argument("--smoke-screen", type=Path, help="GUI 렌더 검증용 PNG 저장 후 종료")
    parser.add_argument("--smoke-media", type=Path, help="렌더 검증과 함께 복사본 MP3를 음소거 재생")
    parser.add_argument("--smoke-api", action="store_true", help="렌더 검증과 함께 등록된 키의 모델 목록만 조회")
    parser.add_argument('--smoke-sdk', action='store_true', help='SDK 직렬화·응답 검증. 네트워크·유료 요청 없이 수행')
    args = parser.parse_args(argv)
    if args.smoke_media and not args.smoke_screen:
        parser.error("--smoke-media는 --smoke-screen과 함께 사용하세요.")
    if args.smoke_api and not args.smoke_screen:
        parser.error("--smoke-api는 --smoke-screen과 함께 사용하세요.")
    if args.smoke_sdk and not args.smoke_screen:
        parser.error('--smoke-sdk는 --smoke-screen과 함께 사용하세요.')
    app = QApplication(sys.argv[:1])
    QLoggingCategory.setFilterRules("qt.multimedia.ffmpeg.*=false")
    app.setApplicationName("music-sorter")
    app.setOrganizationName("music-sorter")
    try:
        args.data_dir = args.data_dir or data_directory()
        args.data_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError):
        QMessageBox.warning(None, '데이터 위치 확인', '지정한 데이터 위치를 열 수 없습니다. 폴더 연결·위치 설정을 확인하세요.')
        return 1
    lock = QLockFile(str(args.data_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(None, "이미 실행 중", "이 데이터 디렉터리는 다른 music-sorter가 사용 중입니다.")
        return 1
    try:
        config_path = args.data_dir / "settings.json"
        settings = Settings.load(config_path)
        if args.profile:
            from .profiles import load_profile
            from .settings import CredentialStore
            profile = load_profile(args.profile, settings)
            settings = profile.settings
            CredentialStore.profile_keys, CredentialStore.profile_defaults = profile.keys, profile.job_defaults
        library = Library(args.data_dir / "music-sorter.sqlite3")
        from .catalog import load_catalog
        from .classification import TAXONOMY
        catalog = load_catalog(args.data_dir / 'taxonomy.json')
        TAXONOMY.clear()
        TAXONOMY.update(catalog)
        library.recover_interrupted_jobs()
        from .classifier import Classifier
        Classifier(library).recover()
        apply_theme(app, settings.theme, settings.font_scale)
        window = MainWindow(library, settings, config_path)
        if args.profile:
            window.status.setText('명시적 개발 프로필 활성 · 프로필/환경 변수 키만 사용 · 미지원 설정: ' + (', '.join(profile.ignored) or '없음'))
        if settings.music_root:
            recovery = FileOperations(library, Path(settings.music_root)).recover()
            if recovery['recovered'] or recovery['pending']:
                window.refresh()
                window.status.setText(f'파일 작업 기록 복구 {recovery["recovered"]} · 확인 필요 {len(recovery["pending"])} · 작업 이력에서 확인하세요')
        window.show()
        api_dialog = None
        api_report = {}
        sdk_report = None
        if args.smoke_sdk:
            from .selfcheck import verify_sdks
            sdk_report = verify_sdks()
        if args.smoke_api:
            api_dialog = SettingsDialog(settings, config_path, library, window)
            api_dialog.menu.setCurrentRow(2)
            api_dialog.show()
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
                if sdk_report:
                    report['sdk_selfcheck'] = sdk_report
                if api_dialog:
                    report["connections"] = api_report
                    report["generation_requests"] = 0
                args.smoke_screen.with_suffix(".json").write_text(json.dumps(report, indent=2), "utf-8")
                if not (api_dialog or window).grab().save(str(args.smoke_screen)):
                    app.exit(2)
                elif args.smoke_media and not (media_loaded and advanced):
                    app.exit(3)
                elif sdk_report and not sdk_report['completed']:
                    app.exit(5)
                elif any(value["status"] == "error" for value in api_report.values()):
                    app.exit(4)
                else:
                    window.player.stop()
                    app.quit()
            if api_dialog:
                services = iter(("openai", "anthropic"))

                def check_next():
                    provider = next(services, None)
                    if provider is None:
                        QTimer.singleShot(700, snapshot)
                        return
                    if not api_dialog.connection_buttons[provider].isEnabled():
                        status = "not_registered" if api_dialog.key_states[provider].text() == "미등록" else "error"
                        api_report[provider] = {"status": status}
                        QTimer.singleShot(0, check_next)
                        return
                    api_dialog.check_connection(provider)
                    worker = api_dialog.connection_worker

                    def checked():
                        api_report[provider] = {"status": "verified" if worker.outcome is not None else "error",
                                                "models": len(worker.outcome.models) if worker.outcome else 0}
                        QTimer.singleShot(0, check_next)

                    worker.finished.connect(checked)

                QTimer.singleShot(300, check_next)
            else:
                QTimer.singleShot(2000 if args.smoke_media else 700, snapshot)
        return app.exec()
    except Exception as error:
        if args.smoke_screen:
            args.smoke_screen.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_screen.with_suffix('.json').write_text(json.dumps(dict(rendered=False, error_type=type(error).__name__), indent=2), 'utf-8')
            return 1
        QMessageBox.critical(None, "시작 보류", f"로컬 설정·DB를 열지 못했습니다 ({type(error).__name__}). 원본 데이터를 보존한 채 설정·DB 상태를 확인하세요.")
        return 1
    finally:
        lock.unlock()
