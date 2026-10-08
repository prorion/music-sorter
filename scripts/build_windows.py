"""Reproducible local Qt standalone build; generated paths stay outside Git."""
import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from music_sorter import __version__


def ensure_not_running(bundle):
    """Refuse to replace any deployed runtime that Windows is still using."""
    if os.name != 'nt':
        return
    environment = dict(os.environ, MUSIC_SORTER_BUNDLE_PREFIX=str(bundle.resolve()) + os.sep)
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = """
$ErrorActionPreference = 'Stop'
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:MUSIC_SORTER_BUNDLE_PREFIX, [StringComparison]::OrdinalIgnoreCase)
})
if ($running.Count -gt 0) { throw '배포 폴더의 앱을 종료한 뒤 다시 빌드하세요. 기존 배포는 유지됩니다.' }
"""
    subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                   env=environment, check=True)


def write_build_info(root, bundle, version):
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                            capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD'], cwd=root).returncode
    if dirty not in (0, 1):
        raise RuntimeError('빌드의 Git 변경 상태를 확인할 수 없습니다.')
    info = dict(version=version, source_commit=commit, tracked_changes=bool(dirty),
                built_at_utc=datetime.now(timezone.utc).isoformat())
    (bundle / 'build-info.json').write_text(json.dumps(info, indent=2), 'utf-8')


def publish_bundle(root, bundle, output):
    """Swap the entire runtime; restore the old one if publication fails."""
    root, bundle, output = root.resolve(), bundle.resolve(), output.resolve()
    if output == root or output in root.parents or bundle == output or bundle in output.parents or output in bundle.parents:
        raise ValueError('프로젝트·임시 빌드와 분리된 출력 폴더를 지정하세요.')
    if bundle.drive.lower() != output.drive.lower():
        raise ValueError('폴더 교체와 복구를 위해 프로젝트와 같은 드라이브의 출력 폴더를 지정하세요.')
    if not (bundle / 'main.exe').is_file() or not (bundle / 'DEPENDENCIES.json').is_file():
        raise ValueError('실행파일과 의존성 고지가 완성된 빌드만 배포할 수 있습니다.')
    if output.exists() and any(output.iterdir()) and not (
            (output / 'main.exe').is_file() and (output / 'DEPENDENCIES.json').is_file()):
        raise ValueError('기존 배포가 아닌 폴더는 교체하지 않습니다. 빈 폴더나 배포 폴더를 지정하세요.')
    ensure_not_running(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = root / 'build' / ('previous-bundle-' + uuid.uuid4().hex)
    backup.parent.mkdir(exist_ok=True)
    had_output = output.exists()
    if had_output:
        output.rename(backup)
    try:
        bundle.rename(output)
        write_shortcut(root, output)
    except BaseException:
        if output.exists():
            output.rename(bundle)
        if had_output:
            backup.rename(output)
        raise
    if had_output:
        print(f'이전 배포 복구본: {backup}')
    print(f'배포 실행파일: {output / "main.exe"}')


def write_shortcut(root, bundle):
    """Publish one fixed Windows entry point without separating its runtime files."""
    executable = (bundle / 'main.exe').resolve(strict=True)
    if not executable.is_file():
        raise ValueError('바로가기 대상 실행파일을 확인하세요.')
    build = root / 'build'
    build.mkdir(exist_ok=True)
    temporary = build / f'shortcut-{uuid.uuid4().hex}.lnk'
    environment = dict(os.environ, MUSIC_SORTER_SHORTCUT_PATH=str(temporary),
                       MUSIC_SORTER_SHORTCUT_TARGET=str(executable), MUSIC_SORTER_SHORTCUT_ROOT=str(root))
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = """
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($env:MUSIC_SORTER_SHORTCUT_PATH)
$shortcut.TargetPath = $env:MUSIC_SORTER_SHORTCUT_TARGET
$shortcut.WorkingDirectory = $env:MUSIC_SORTER_SHORTCUT_ROOT
$shortcut.Description = 'music-sorter'
$shortcut.IconLocation = $env:MUSIC_SORTER_SHORTCUT_TARGET + ',0'
$shortcut.Save()
$saved = $shell.CreateShortcut($env:MUSIC_SORTER_SHORTCUT_PATH)
if ($saved.TargetPath -ne $env:MUSIC_SORTER_SHORTCUT_TARGET -or $saved.WorkingDirectory -ne $env:MUSIC_SORTER_SHORTCUT_ROOT) {
    throw 'Shortcut verification failed'
}
"""
    try:
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                       env=environment, check=True)
        temporary.replace(root / 'music-sorter.lnk')
    finally:
        temporary.unlink(missing_ok=True)


def write_notices(bundle, version):
    license_root = bundle / 'third-party-licenses'
    license_root.mkdir(exist_ok=True)
    pending, seen, notices = ['PySide6', 'mutagen', 'keyring', 'openai', 'anthropic', 'pywin32'], set(), []
    while pending:
        name = pending.pop()
        key = canonicalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        package = distribution(name)
        notices.append({'name': package.metadata['Name'], 'version': package.version,
                        'license': package.metadata.get('License-Expression', package.metadata.get('License', 'unreported'))})
        for requirement in package.requires or []:
            dependency = Requirement(requirement)
            if dependency.marker is None or dependency.marker.evaluate({'extra': ''}):
                pending.append(dependency.name)
        for item in package.files or []:
            if ('license' in str(item).lower() or 'copying' in str(item).lower()) and '..' not in item.parts:
                source = package.locate_file(item)
                if source.is_file():
                    target = license_root / key / str(item)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, license_root / 'Python-LICENSE.txt')
    (bundle / 'DEPENDENCIES.json').write_text(json.dumps(sorted(notices, key=lambda p: p['name']), indent=2), 'utf-8')
    (bundle / 'README.txt').write_text(
        f'music-sorter {version} · Windows x64\n\n'
        '압축을 풀고 main.exe를 실행하세요. DLL·리소스를 포함한 폴더 전체가 필요합니다.\n'
        '일반 설정과 음악 DB는 사용자 앱 데이터에, API 키는 Windows 자격 증명 저장소에 보관합니다.\n'
        '이전 버전을 종료하고 새 버전을 실행하면 기존 데이터와 등록 키를 사용합니다.\n\n'
        '1. 설정 → 음악 라이브러리에서 폴더를 등록하고 폴더 스캔을 실행합니다.\n'
        '2. 설정 → LLM / API 연결에서 OpenAI·Claude 키를 등록하고 모델을 조회합니다.\n'
        '3. 분류 실행에서 대상·전송 입력·작업 예산을 확인하고 별도로 제출합니다.\n'
        '   중복 검토에서는 삭제할 행을 선택하고 확인 후 휴지통으로 이동할 수 있습니다.\n'
        '   복원은 Windows 휴지통에서 한 뒤 폴더를 재스캔합니다. 영구 삭제는 제공하지 않습니다.\n'
        '4. 파일 정리 미리보기에서 변경 경로를 확인한 뒤 실제 적용을 선택합니다.\n'
        '5. 작업 이력에서 중단 작업을 재개하거나 파일 변경을 되돌릴 수 있습니다.\n'
        '6. 재생목록 메뉴에서 태그 목록과 조합 목록을 생성합니다.\n\n'
        '파일 변경의 첫 검증에는 별도 음악 복사본을 사용하세요.\n'
        '기본 M3U8은 곰오디오의 한글 경로·장르·재생을 확인했습니다.\n'
        'Claude 실제 동기 생성·Batch 제출/수집·취소와 응답 사용량 기반 비용을 검증했습니다.\n'
        '분류 정답률·OpenAI 실제 생성·삼성 뮤직·별도 Windows 환경 검증은 남아 있습니다.\n'
        '설치 의존성과 고지는 DEPENDENCIES.json 및 third-party-licenses 폴더에 포함합니다.\n',
        'utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="실행파일·DLL을 바로 아래에 둘 출력 폴더 (기본: dist)")
    parser.add_argument('--shortcut-only', action='store_true', help='기존 빌드를 가리키는 프로젝트 루트 바로가기만 생성')
    parser.add_argument('--notices-only', action='store_true', help='기존 빌드의 실제 설치 의존성·라이선스 목록만 작성')
    parser.add_argument('--bundle-version', help='기존 빌드 고지에 기록할 버전')
    args = parser.parse_args()
    if args.shortcut_only and (args.dry_run or args.notices_only):
        parser.error('--shortcut-only는 --dry-run 또는 --notices-only와 함께 사용할 수 없습니다.')
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve() if args.output_dir else root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    if args.shortcut_only:
        write_shortcut(root, output)
        return 0
    if args.notices_only:
        write_notices(output, args.bundle_version or __version__)
        return 0
    if not args.dry_run:
        ensure_not_running(output)
    build = root / "build"
    build.mkdir(exist_ok=True)
    staging = build / ('windows-output-' + uuid.uuid4().hex)
    staging.mkdir()
    config = configparser.ConfigParser()
    config["app"] = {"title": "music-sorter", "project_dir": str(root), "input_file": str(root / "main.py"),
                     "exec_directory": str(staging), "project_file": "", "icon": ""}
    config["python"] = {"python_path": sys.executable, "packages": "Nuitka==4.2.2"}
    config["qt"] = {"modules": "Core,Gui,Widgets,Multimedia", "plugins": "multimedia,platforms,imageformats,styles",
                    "qml_files": "", "excluded_qml_plugins": ""}
    config["nuitka"] = {"mode": "standalone", "macos.permissions": "",
                        "extra_args": "--quiet --noinclude-qt-translations --include-package=music_sorter "
                        "--include-package-data=music_sorter --windows-console-mode=disable --msvc=latest "
                        "--include-package=openai.resources.responses --include-package=openai.types.responses "
                        "--include-module=openai.resources.files --include-module=openai.resources.batches "
                        "--include-package=anthropic.resources.messages --include-package=anthropic.types "
                        "--include-module=pythoncom --include-module=pywintypes --include-module=win32file "
                        "--include-module=win32com.shell.shell --include-module=win32com.shell.shellcon "
                        "--include-module=win32com.server.policy --include-module=win32com.server.exception "
                        "--assume-yes-for-downloads"}
    target = build / "windows.spec"
    with target.open("w", encoding="utf-8") as stream:
        config.write(stream)
    executable = Path(sys.executable).parent / "pyside6-deploy.exe"
    command = [str(executable), str(root / "main.py"), "--config-file", str(target), "--force",
               "--keep-deployment-files", "--nuitka-version=4.2.2", "--extra-ignore-dirs=files,artifacts,tests,scripts,docs,build,dist,deployment"]
    if args.dry_run:
        command.append("--dry-run")
    result = subprocess.call(command, cwd=root)
    if result != 0 or args.dry_run:
        return result
    bundle = staging / "music-sorter.dist"
    write_notices(bundle, __version__)
    write_build_info(root, bundle, __version__)
    publish_bundle(root, bundle, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
