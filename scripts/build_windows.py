"""Reproducible local Qt standalone build; generated paths stay outside Git."""
import argparse
import configparser
import json
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from music_sorter import __version__


def write_notices(bundle, version):
    license_root = bundle / 'third-party-licenses'
    license_root.mkdir(exist_ok=True)
    pending, seen, notices = ['PySide6', 'mutagen', 'keyring', 'openai', 'anthropic'], set(), []
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
    (bundle / 'README.txt').write_text(f'music-sorter {version}\nRun main.exe; keep this whole folder.\n'
        'File preview/apply/undo, relative playlists, budgeted OpenAI/Claude sync and Batch are implemented.\n'
        'Settings include vault keys, explicit profiles, taxonomy, DB restore and verified data migration.\n'
        'Use copies to validate file changes. Actual generation/quality and target-device compatibility require validation.\n'
        'This is a locally verified build; separate Windows and full distribution notices review remain pending.\n', 'utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="실행 중인 이전 빌드를 보존할 별도 출력 폴더")
    parser.add_argument('--notices-only', action='store_true', help='기존 빌드의 실제 설치 의존성·라이선스 목록만 작성')
    parser.add_argument('--bundle-version', help='기존 빌드 고지에 기록할 버전')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve() if args.output_dir else root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    if args.notices_only:
        write_notices(output / 'music-sorter.dist', args.bundle_version or __version__)
        return 0
    build = root / "build"
    build.mkdir(exist_ok=True)
    config = configparser.ConfigParser()
    config["app"] = {"title": "music-sorter", "project_dir": str(root), "input_file": str(root / "main.py"),
                     "exec_directory": str(output), "project_file": "", "icon": ""}
    config["python"] = {"python_path": sys.executable, "packages": "Nuitka==4.2.2"}
    config["qt"] = {"modules": "Core,Gui,Widgets,Multimedia", "plugins": "multimedia,platforms,imageformats,styles",
                    "qml_files": "", "excluded_qml_plugins": ""}
    config["nuitka"] = {"mode": "standalone", "macos.permissions": "",
                        "extra_args": "--quiet --noinclude-qt-translations --include-package=music_sorter "
                        "--include-package-data=music_sorter --windows-console-mode=disable --msvc=latest "
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
    bundle = output / "music-sorter.dist"
    write_notices(bundle, __version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
