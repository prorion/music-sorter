# music-sorter

Windows용 mp3 음악 자동 분류 및 재생목록 생성기

대량의 mp3 파일을 **장르 / 분위기 / 컨셉**으로 자동 분류하고,
태그별 재생목록(M3U8 기본, M3U 선택)을 만들어 일반 음악 앱에서 들을 수 있게 합니다.

> **0.7.9 소스 / 0.7.8 배포:** 국내 음악 사이트 검색·출처 비교를 추가했습니다. 멜론·벅스를 우선 대조하고 충돌·근거 부족 시 지니를 추가 조회합니다. 조회에는 LLM을 호출하지 않으며 확인된 참고 장르만 AI 분류 입력에 전달합니다. 최신 빌드와 검증 상태는 SPEC §17에서 관리합니다.

Windows GUI와 설정 메뉴를 제공하고 OpenAI·Claude API를 선택해 사용하도록 설계했습니다. 결정 상태·남은 검증은 [SPEC §14](docs/SPEC.md#14-결정-상태와-남은-검증), 개발 순서는 [SPEC §16](docs/SPEC.md#16-첫-버전-범위와-개발-순서), 실제 구현 현황은 [SPEC §17](docs/SPEC.md#17-구현검증-현황)을 봅니다.

## 문서

- [설계 문서 (정본)](docs/SPEC.md)
- [결정 이력](docs/DECISIONS.md)
- [논의 히스토리](docs/HISTORY.md)
- [작업 규칙](CLAUDE.md)

## 설정

설정 항목과 개발용 입력 예시는 [`.env.example`](.env.example), GUI 메뉴·저장·우선순위의 정본은 [SPEC §15](docs/SPEC.md#15-gui와-설정-메뉴)입니다. 실제 개인 설정과 키는 로컬에 보관하며 저장소에 넣지 않습니다. GUI 일반 설정·Windows 키 저장·OpenAI/Claude 연결 확인과 명시적 `.env` 프로필을 제공합니다. `--profile .env`로 지정한 세션에서만 프로필과 환경 변수를 읽습니다. 지원 밖 항목은 이름을 표시하며 적용하지 않습니다.

`⚙ 설정 → AI / API 연결`에서 해당 서비스의 `등록 / 교체`로 키를 저장하고 `연결 확인 / 모델 조회`를 누릅니다. 연결 확인 후 `분류와 비용`에서 모델을 선택합니다. 실제 분류는 음악 목록의 `AI로 분류`에서 곡·예산을 선택하고 `예상 비용 확인` → `AI 분류 시작 (유료)` 순서로 진행합니다. `곡 정보 찾기`는 MusicBrainz·Last.fm에서 참고 정보를 조회하며 현재 분류를 바꾸지 않습니다.

## 개발 환경과 실행

빌드된 앱은 **`dist/main.exe`** 또는 프로젝트 루트의 **`music-sorter.lnk` 바로가기**를 더블클릭해 실행합니다. 실행파일과 DLL·리소스는 `dist` 바로 아래에 함께 보관하며 버전별 폴더를 만들지 않습니다. 버전은 앱 내부·`dist/build-info.json`·Git 이력으로 관리합니다. 프로젝트를 이동했거나 기존 빌드에 다시 연결하려면 아래 명령으로 바로가기만 생성합니다.

```powershell
.\.venv\Scripts\python.exe scripts\build_windows.py --shortcut-only
```

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

기본 DB·일반 설정은 로컬 사용자 앱 데이터에 저장합니다. 실행 후 폴더를 등록하고 스캔합니다. API 키 없이 로컬 기능을 사용할 수 있습니다. 파일 변경은 미리보기 뒤 명시적으로 실제 적용을 선택해야 시작합니다. 유료 분류는 키·확인된 모델 단가·작업 예산과 별도 제출이 필요합니다. 원격 Batch는 앱 종료 후에도 처리될 수 있으며 작업 기록에서 상태·결과를 수집합니다.

## 앱에서 사용하는 순서

1. `⚙ 설정 → 음악 라이브러리`에서 폴더를 선택하고 저장한 다음 `폴더 스캔`을 실행합니다. 첫 검증은 별도 음악 사본 폴더를 사용합니다.
2. `Ctrl+F`로 검색하고 곡을 선택해 상세에서 수동 판정을 저장하거나 `AI로 분류`에서 전송 입력과 예산을 확인합니다. 수동 수정한 항목은 자동 변경에서 보호합니다.
3. `곡 정보 찾기`은 외부 후보·참고 태그 조회입니다. 결과를 두 번 클릭해 근거와 후보를 검토합니다. `분류 확인`에서 미확정 항목과 제안을 해결하고 `중복 곡 확인`에서 남길 파일을 체크합니다. 삭제할 파일은 행을 선택(Ctrl/Shift로 여러 개)하고 `선택 파일 삭제 · 휴지통`에서 확인합니다. 복원은 Windows 휴지통에서 한 뒤 폴더를 재스캔합니다.
4. `파일 정리 미리보기`에서 이동·이름·장르 기록을 선택하고 원래/예정 경로와 보류 이유를 확인합니다. 실제 적용은 별도 버튼으로 실행합니다. `작업 기록`에서 중단된 작업과 되돌리기를 확인합니다.
5. `재생목록 → 재생목록 만들기·관리`에서 기본 목록과 선택한 AND 조합을 생성합니다. 음악 폴더 전체를 옮기면 상대 경로를 유지할 수 있습니다. 실제 대상 앱 호환 여부는 SPEC의 검증 상태를 확인하세요.

`설정 → 데이터·복구`의 DB 복원은 음악 파일 되돌리기와 별개입니다. 복원·이관 전에 검증 사본과 기존 데이터 보존 상태를 표시합니다. 태그 목록 변경은 새 작업부터 적용하며 기존 판정·진행 작업의 분류 목록은 유지합니다.

## 검증과 빌드

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\verify_local.py --source files --output artifacts\new-verification
.\.venv\Scripts\python.exe scripts\verify_review.py --source artifacts\new-verification\sample-library --output artifacts\new-review
.\.venv\Scripts\python.exe scripts\benchmark_library.py --output artifacts\new-benchmark
.\.venv\Scripts\python.exe scripts\verify_scale_memory.py --db artifacts\new-benchmark\benchmark.sqlite3 --output artifacts\new-benchmark\memory.json
.\.venv\Scripts\python.exe scripts\prepare_evaluation.py --source files --output artifacts\new-evaluation-review
.\.venv\Scripts\python.exe scripts\build_windows.py
```

빌드는 `build`의 임시 폴더에서 마친 뒤 `dist`를 통째로 교체합니다. 실행 중이면 앱 종료를 안내하고 기존 배포를 유지하며 교체·바로가기 갱신 실패 시 이전 배포로 복구합니다. 이전 결과는 `build`의 내부 복구 폴더에 보존합니다. `--output-dir`을 지정하면 같은 드라이브의 해당 폴더 바로 아래에 출력합니다. 실제 앱 검증 근거는 SPEC §17.13, 고정 경로 검증은 §17.14에서 확인합니다. 기존 사용자 데이터와 등록 키는 별도 보관하므로 빌드 교체 뒤에도 유지됩니다.

등록된 키의 읽기 전용 실제 연결 검증은 아래처럼 명시적으로 실행합니다. 모델 목록 GET만 요청하며 키나 응답 원문은 출력하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_connections.py --live --output artifacts\new-api-check\live.json
.\dist\main.exe --data-dir artifacts\new-api-check\user-data --smoke-sdk --smoke-api --smoke-screen artifacts\new-api-check\packaged.png
```

Claude의 무료 입력 계량과 실제 서버의 구조화 스키마 수용 여부는 `scripts/verify_token_count.py --live --output artifacts/new-token-check.json`으로 확인합니다. 이 검증은 합성 입력만 전송하고 생성·Batch 제출은 하지 않습니다. 전체 배포 검증은 `scripts/verify_bundle.py --bundle dist --media-copy <검증용MP3사본> --output artifacts/new-bundle-check --live-api`로 한글 경로의 실행·SDK·재생·모델 조회와 폴더 전체 ZIP 무결성을 확인합니다. Python 관련 환경을 제거한 현재 PC의 확인이며 Python 미설치 별도 Windows 검증과 구분합니다.

검증 출력 디렉터리는 새 이름으로 지정합니다. 음악 검증은 원본을 읽어 복사본을 만든 뒤 수행합니다. 벤치마크는 합성 DB이며 실제 음악 라이브러리의 처리 속도를 보증하지 않습니다.

검토 집합은 같은 해시·녹음 관계를 묶고 조정 120/최종 80곡으로 고정합니다. 검토 사본의 별도 DB에서 사람이 각 항목을 수동 보호된 정답으로 저장한 뒤, 정답을 입력하지 않은 별도 모델 판정 DB와 비교합니다. `scripts/evaluate_classification.py --manifest <review-manifest.json> --gold-db <정답DB> --prediction-db <모델DB> --output <평가.json>`으로 항목별 정답률·확정 비율·태그 precision/recall을 계산합니다. 미검수·LLM 정답 초안·검증용 수동 입력을 실제 정답률로 보고하지 않습니다.

빌드는 `pyside6-deploy`·Nuitka·설치된 Visual Studio C 도구를 사용합니다. 결과는 `dist/main.exe`와 의존 파일이며 `dist` 폴더 전체가 필요합니다. 생성 설정·검증 자료·실행 파일은 Git에서 제외합니다. 별도 Windows 환경 검증과 배포 라이선스 검토는 출시 전 과제입니다.
