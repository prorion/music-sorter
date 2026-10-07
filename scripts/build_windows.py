"""Reproducible local Qt standalone build; generated paths stay outside Git."""
import argparse
import configparser
import json
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = root / "build"
    build.mkdir(exist_ok=True)
    config = configparser.ConfigParser()
    config["app"] = {"title": "music-sorter", "project_dir": str(root), "input_file": str(root / "main.py"),
                     "exec_directory": str(root / "dist"), "project_file": "", "icon": ""}
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
    bundle = root / "dist" / "music-sorter.dist"
    license_root = bundle / "third-party-licenses"
    license_root.mkdir(exist_ok=True)
    notices = []
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "mutagen", "keyring",
                 "pywin32-ctypes", "jaraco.classes", "jaraco.functools", "jaraco.context", "more-itertools"):
        package = distribution(name)
        notices.append({"name": name, "version": package.version,
                        "license": package.metadata.get("License-Expression", package.metadata.get("License", "unreported"))})
        for item in package.files or []:
            if "license" in str(item).lower() or "copying" in str(item).lower():
                source = package.locate_file(item)
                if source.is_file():
                    destination = license_root / name / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    (bundle / "DEPENDENCIES.json").write_text(json.dumps(notices, indent=2), "utf-8")
    (bundle / "README.txt").write_text("music-sorter 0.1 local development prototype\nRun main.exe.\n"
                                      "API classification and music file changes are not implemented.\n"
                                      "Separate Windows validation and full distribution license review remain pending.\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
