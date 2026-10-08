"""Copy a finished standalone bundle; smoke in a Unicode path with no Python env."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--media-copy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live-api', action='store_true', help='등록된 키의 모델 목록 GET만 확인')
    parser.add_argument('--recycle', action='store_true', help='새 검증 사본의 네이티브 휴지통 이동도 확인')
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    if output.exists() or bundle == output or bundle in output.parents or output in bundle.parents:
        raise ValueError('완성된 배포 폴더 밖의 새 검증 폴더를 사용하세요.')
    if not (bundle / 'main.exe').is_file() or not (bundle / 'DEPENDENCIES.json').is_file():
        raise ValueError('고지 수집까지 완료한 배포 폴더가 필요합니다.')
    files = [path for path in bundle.rglob('*') if path.is_file()]
    forbidden = [str(path.relative_to(bundle)) for path in files
                 if path.suffix.lower() in {'.mp3', '.sqlite3', '.sqlite', '.db'}
                 or path.name.lower().startswith('.env')
                 or path.name.lower() in {'settings.json', 'credentials.json', 'app.lock', 'data-location.json'}]
    if forbidden:
        raise ValueError('배포 폴더에 개인 음악·데이터·설정이 있습니다.')
    output.mkdir(parents=True)
    copied = output / '한글 경로 실행 확인' / 'music-sorter.dist'
    shutil.copytree(bundle, copied)
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper().startswith(('PYTHON', 'CONDA', 'VIRTUAL_ENV', 'QT_PLUGIN', 'QML2_IMPORT')) or key.upper() in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LASTFM_API_KEY'}:
            environment.pop(key)
    environment['PATH'] = str(Path(environment['WINDIR']) / 'System32') + ';' + environment['WINDIR']
    snapshot = output / 'clean-environment.png'
    command = [str(copied / 'main.exe'), '--data-dir', str(output / 'user-data'), '--smoke-sdk',
               '--smoke-media', str(args.media_copy.resolve()), '--smoke-screen', str(snapshot)]
    if args.live_api:
        command.append('--smoke-api')
    if args.recycle:
        command.append('--smoke-recycle')
    result = subprocess.run(command, env=environment, cwd=copied, timeout=180)
    smoke = json.loads(snapshot.with_suffix('.json').read_text('utf-8'))
    if result.returncode != 0 or not smoke.get('sdk_selfcheck', {}).get('completed') or not smoke.get('media_position_advanced'):
        raise ValueError('독립 배포 실행 검증 실패. 출력 JSON을 확인하세요.')
    if args.recycle and not smoke.get('recycle_selfcheck', {}).get('completed'):
        raise ValueError('독립 배포 휴지통 이동 검증 실패.')
    archive = output / 'music-sorter-windows-x64.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in files:
            zipped.write(path, 'music-sorter.dist/' + path.relative_to(bundle).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None or len(zipped.infolist()) != len(files):
            raise ValueError('ZIP 내용·무결성을 확인할 수 없습니다.')
    with archive.open('rb') as stream:
        fingerprint = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = dict(exit_code=result.returncode, bundle_files=len(files), private_files=0, unicode_path=True,
                  python_environment_removed=True, separate_windows_without_python_verified=False,
                  zip=str(archive), zip_bytes=archive.stat().st_size, zip_sha256=fingerprint, smoke=smoke)
    (output / 'report.json').write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
