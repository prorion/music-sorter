# music-sorter

Windows용 mp3 음악 자동 분류 및 재생목록 생성기

대량의 mp3 파일을 **장르 / 분위기 / 컨셉**으로 자동 분류하고,
태그별 재생목록(m3u)을 만들어 PC와 폰의 일반 음악 앱에서 바로 들을 수 있게 합니다.

> 현재 설계 단계입니다.

Windows GUI와 설정 메뉴를 제공하고 OpenAI·Claude API를 선택해 사용하는 방향으로 설계 중입니다.

## 문서

- [설계 문서 (정본)](docs/SPEC.md)
- [결정 이력](docs/DECISIONS.md)
- [논의 히스토리](docs/HISTORY.md)
- [작업 규칙](CLAUDE.md)

## 설정

현재 설정 항목 예시는 [`.env.example`](.env.example)에 있습니다. GUI 설정 구성과 저장 방식은 [SPEC §15](docs/SPEC.md#15-gui와-설정-메뉴)에서 검토 중입니다. `.env`는 커밋되지 않습니다.
