# music-sorter

Windows용 mp3 음악 자동 분류 및 재생목록 생성기

대량의 mp3 파일을 **장르 / 분위기 / 컨셉**으로 자동 분류하고,
태그별 재생목록(m3u)을 만들어 PC와 폰의 일반 음악 앱에서 바로 들을 수 있게 합니다.

> **개발 진행 중:** 2026-10-07 개발 착수. 현재 0.2 로컬 관리 시제품이며 전체 첫 버전이 완성된 상태는 아닙니다.

Windows GUI와 설정 메뉴를 제공하고 OpenAI·Claude API를 선택해 사용하도록 설계했습니다. 결정 상태·남은 검증은 [SPEC §14](docs/SPEC.md#14-결정-상태와-남은-검증), 개발 순서는 [SPEC §16](docs/SPEC.md#16-첫-버전-범위와-개발-순서), 실제 구현 현황은 [SPEC §17](docs/SPEC.md#17-구현검증-현황)을 봅니다.

## 문서

- [설계 문서 (정본)](docs/SPEC.md)
- [결정 이력](docs/DECISIONS.md)
- [논의 히스토리](docs/HISTORY.md)
- [작업 규칙](CLAUDE.md)

## 설정

설정 항목과 개발용 입력 예시는 [`.env.example`](.env.example), GUI 메뉴·저장·우선순위의 정본은 [SPEC §15](docs/SPEC.md#15-gui와-설정-메뉴)입니다. 실제 개인 설정과 키는 로컬에 보관하며 저장소에 넣지 않습니다. 현재 GUI 일반 설정과 Windows 키 저장이 구현됐으며 `.env` 개발 프로필 로더는 아직 제공하지 않습니다.

## 개발 환경과 실행

Windows에서 Python 3.13으로 프로젝트 전용 가상환경을 만듭니다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\pythonw.exe main.py
```

이후에는 `run.ps1`로 실행할 수 있습니다. 별도 데이터로 실행하려면 아래처럼 지정합니다.

```powershell
.\.venv\Scripts\python.exe main.py --data-dir artifacts\my-test-data
```

기본 DB·일반 설정은 로컬 사용자 앱 데이터에 저장합니다. 실행 후 폴더를 등록하고 스캔합니다. API 키 없이 로컬 기능을 사용할 수 있습니다. 현재 유료 분류·음악 이동·태그 기록은 실행하지 않습니다.

## 검증과 빌드

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\verify_local.py --source files --output artifacts\new-verification
.\.venv\Scripts\python.exe scripts\verify_review.py --source artifacts\new-verification\sample-library --output artifacts\new-review
.\.venv\Scripts\python.exe scripts\benchmark_library.py --output artifacts\new-benchmark
.\.venv\Scripts\python.exe scripts\build_windows.py
```

검증 출력 디렉터리는 새 이름으로 지정합니다. 음악 검증은 원본을 읽어 복사본을 만든 뒤 수행합니다. 벤치마크는 합성 DB이며 실제 음악 라이브러리의 처리 속도를 보증하지 않습니다.

빌드는 `pyside6-deploy`·Nuitka·설치된 Visual Studio C 도구를 사용합니다. 결과는 `dist/music-sorter.dist/main.exe`와 의존 파일이며 폴더 전체가 필요합니다. 생성 설정·검증 자료·실행 파일은 Git에서 제외합니다. 별도 Windows 환경 검증과 배포 라이선스 검토는 출시 전 과제입니다.
