# music-sorter

Windows용 mp3 음악 자동 분류 및 재생목록 생성기

대량의 mp3 파일을 **장르 / 분위기 / 컨셉**으로 자동 분류하고,
태그별 재생목록(M3U8 기본, M3U 선택)을 만들어 일반 음악 앱에서 들을 수 있게 합니다.

> **0.7.3:** 밝게/어둡게 UI, 파일 정리·복구·재생목록·외부 녹음 후보 검토·OpenAI/Claude 분류 연결과 데이터/태그/검토 관리를 구현했습니다. 곰오디오에서 한글 경로·장르·재생을 확인한 M3U8을 기본값으로 사용합니다. 실제 생성·과금 사용량·분류 정답률과 별도 장치 검증은 남아 있습니다. 실행 검증 근거는 SPEC §17.10을 확인하세요.

Windows GUI와 설정 메뉴를 제공하고 OpenAI·Claude API를 선택해 사용하도록 설계했습니다. 결정 상태·남은 검증은 [SPEC §14](docs/SPEC.md#14-결정-상태와-남은-검증), 개발 순서는 [SPEC §16](docs/SPEC.md#16-첫-버전-범위와-개발-순서), 실제 구현 현황은 [SPEC §17](docs/SPEC.md#17-구현검증-현황)을 봅니다.

## 문서

- [설계 문서 (정본)](docs/SPEC.md)
- [결정 이력](docs/DECISIONS.md)
- [논의 히스토리](docs/HISTORY.md)
- [작업 규칙](CLAUDE.md)

## 설정

설정 항목과 개발용 입력 예시는 [`.env.example`](.env.example), GUI 메뉴·저장·우선순위의 정본은 [SPEC §15](docs/SPEC.md#15-gui와-설정-메뉴)입니다. 실제 개인 설정과 키는 로컬에 보관하며 저장소에 넣지 않습니다. GUI 일반 설정·Windows 키 저장·OpenAI/Claude 연결 확인과 명시적 `.env` 프로필을 제공합니다. `--profile .env`로 지정한 세션에서만 프로필과 환경 변수를 읽습니다. 지원 밖 항목은 이름을 표시하며 적용하지 않습니다.

`⚙ 설정 → LLM / API 연결`에서 해당 서비스의 `등록 / 교체`로 키를 저장하고 `연결 확인 / 모델 조회`를 누릅니다. `연결 확인됨`과 조회 모델 수가 표시되면 `분류와 비용`에서 모델을 선택할 수 있습니다. 연결 확인은 모델 목록 조회입니다. 실제 분류는 라이브러리의 `분류 실행`에서 USD 예산과 전송 입력 계획을 확인한 뒤 별도로 제출합니다. `음악 정보 보완`은 MusicBrainz 연락처·Last.fm 키가 설정된 출처만 조회하며 분류를 바꾸지 않습니다.

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

기본 DB·일반 설정은 로컬 사용자 앱 데이터에 저장합니다. 실행 후 폴더를 등록하고 스캔합니다. API 키 없이 로컬 기능을 사용할 수 있습니다. 파일 변경은 미리보기 뒤 명시적으로 실제 적용을 선택해야 시작합니다. 유료 분류는 키·확인된 모델 단가·작업 예산과 별도 제출이 필요합니다. 원격 Batch는 앱 종료 후에도 처리될 수 있으며 작업 이력에서 상태·결과를 수집합니다.

## 앱에서 사용하는 순서

1. `⚙ 설정 → 음악 라이브러리`에서 폴더를 선택하고 저장한 다음 `폴더 스캔`을 실행합니다. 첫 검증은 별도 음악 사본 폴더를 사용합니다.
2. `Ctrl+F`로 검색하고 곡을 선택해 상세에서 수동 판정을 저장하거나 `분류 실행`에서 전송 입력과 예산을 확인합니다. 수동 수정한 항목은 자동 변경에서 보호합니다.
3. `음악 정보 보완`은 외부 후보·참고 태그 조회입니다. 결과를 두 번 클릭해 근거와 후보를 검토합니다. `분류 검토`에서 미확정 항목과 제안을 해결하고 `중복 검토`에서 남길 파일을 선택합니다.
4. `파일 정리 미리보기`에서 이동·이름·장르 기록을 선택하고 원래/예정 경로와 보류 이유를 확인합니다. 실제 적용은 별도 버튼으로 실행합니다. `작업 이력`에서 중단된 작업과 되돌리기를 확인합니다.
5. `재생목록 → 재생목록 관리·생성`에서 기본 목록과 선택한 AND 조합을 생성합니다. 음악 폴더 전체를 옮기면 상대 경로를 유지할 수 있습니다. 실제 대상 앱 호환 여부는 SPEC의 검증 상태를 확인하세요.

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

기존 실행파일을 사용 중이면 `scripts\build_windows.py --output-dir dist/0.7.3`으로 별도 빌드할 수 있습니다. 0.7.3 실행파일은 `dist/0.7.3/music-sorter.dist/main.exe`입니다. 실제 실행 검증 여부는 SPEC §17.10에서 확인합니다. 이전 버전을 종료한 뒤 새 실행파일을 열면 기존 데이터와 등록 키를 사용합니다. 앱 데이터는 실행파일 폴더와 별도 보관합니다.

등록된 키의 읽기 전용 실제 연결 검증은 아래처럼 명시적으로 실행합니다. 모델 목록 GET만 요청하며 키나 응답 원문은 출력하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_connections.py --live --output artifacts\new-api-check\live.json
.\dist\0.7.3\music-sorter.dist\main.exe --data-dir artifacts\new-api-check\user-data --smoke-sdk --smoke-api --smoke-screen artifacts\new-api-check\packaged.png
```

Claude의 무료 입력 계량과 실제 서버의 구조화 스키마 수용 여부는 `scripts/verify_token_count.py --live --output artifacts/new-token-check.json`으로 확인합니다. 이 검증은 합성 입력만 전송하고 생성·Batch 제출은 하지 않습니다. 전체 배포 검증은 `scripts/verify_bundle.py --bundle dist/0.7.3/music-sorter.dist --media-copy <검증용MP3사본> --output artifacts/new-bundle-check --live-api`로 한글 경로의 실행·SDK·재생·모델 조회와 폴더 전체 ZIP 무결성을 확인합니다. Python 관련 환경을 제거한 현재 PC의 확인이며 Python 미설치 별도 Windows 검증과 구분합니다.

검증 출력 디렉터리는 새 이름으로 지정합니다. 음악 검증은 원본을 읽어 복사본을 만든 뒤 수행합니다. 벤치마크는 합성 DB이며 실제 음악 라이브러리의 처리 속도를 보증하지 않습니다.

검토 집합은 같은 해시·녹음 관계를 묶고 조정 120/최종 80곡으로 고정합니다. 검토 사본의 별도 DB에서 사람이 각 항목을 수동 보호된 정답으로 저장한 뒤, 정답을 입력하지 않은 별도 모델 판정 DB와 비교합니다. `scripts/evaluate_classification.py --manifest <review-manifest.json> --gold-db <정답DB> --prediction-db <모델DB> --output <평가.json>`으로 항목별 정답률·확정 비율·태그 precision/recall을 계산합니다. 미검수·LLM 정답 초안·검증용 수동 입력을 실제 정답률로 보고하지 않습니다.

빌드는 `pyside6-deploy`·Nuitka·설치된 Visual Studio C 도구를 사용합니다. 결과는 `dist/music-sorter.dist/main.exe`와 의존 파일이며 폴더 전체가 필요합니다. 생성 설정·검증 자료·실행 파일은 Git에서 제외합니다. 별도 Windows 환경 검증과 배포 라이선스 검토는 출시 전 과제입니다.
