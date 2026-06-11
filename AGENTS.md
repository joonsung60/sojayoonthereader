# AGENTS.md - Claude / Gemini Code 작업 규칙

## 기본 원칙
1. 실행 전 반드시 코드/변경사항을 보여주고 확인을 받는다. (명시적 지시가 있는 경우 제외)
2. 파일 생성/수정 전 항상 의도를 먼저 설명한다.
3. 불필요한 py 파일을 덕지덕지 만들지 않는다.
4. 목적 달성을 위해 임시 스크립트가 필요하면 작업 후 삭제한다.

## 파일 구조 규칙
메인 파일은 세 개뿐이다.
- `scrape_poems.py` : 위키소스 스크래핑 + 현대어 변환
- `poem_tts.py` : TTS 파이프라인 (행별 파라미터 → wav → 전집 → 타임스탬프)
- `poem_video.py` : 영상 생성 (프레임 합성 → ffmpeg → mp4)

새 기능은 반드시 위 파일들 안에 함수로 추가한다.
`poem_video.py`는 `poem_tts.py`의 상수/함수를 import해서 사용 (단방향 의존).
별도 py 파일 생성 금지 (명시적 허락 없이).
*(참고: RAG 목적의 reference 기능은 품이 많이 들어 현재 진행하지 않으므로 관련된 추가 파일이나 구조도 생성하지 않는다.)*

## 하드코딩 금지
- 시인 이름, 경로, 시집 이름을 코드에 직접 박지 않는다.
- 시인 이름과 시집 이름은 `config.json`의 `active` 항목에서 동적으로 불러와 처리한다 (`AUTHOR`, `COLLECTION`).
- 경로는 반드시 동적으로 할당된 `AUTHOR_DIR`, `AUDIO_DIR`, `LINES_DIR` 등의 변수/상수를 통해 파생한다.

## 폴더 구조 준수
```text
output/{시인}/audio/    ← wav 파일만
output/{시인}/lines/    ← params.json, 타임스탬프, lines_*/ 폴더
output/{시인}/video/    ← mp4 파일만 (배경 이미지 background.png 포함)
poems/raw/{시인}/       ← 원문 txt
poems/modern/{시인}/    ← 현대어 변환 JSON (예: 윤동주.json)
```
- 파일을 엉뚱한 폴더에 저장하지 않는다.
- 폴더 구조 변경이 필요하면 반드시 먼저 확인한다.

## 코드 스타일
- 함수는 범용적으로 설계한다 (특정 시인에 종속 금지).
- 비슷한 기능은 파라미터로 분기하고 함수를 따로 만들지 않는다.
- argparse로 실행 모드를 분리한다 (`--mode full/regen/rebuild/timestamps` 등).

## 실행 규칙
- "코드만 보여줄 것", "실행 금지" 명시 시 절대 실행하지 않는다.
- API 호출이 발생하는 작업은 반드시 사전 확인 후 실행한다 (타입캐스트 API, Claude API, ollama 등).
- ffmpeg 인코딩(`poem_video.py` 전체 영상 생성)처럼 오래 걸리는 작업도 가급적 사전 확인 후 실행한다. 프레임 렌더링 확인은 `--test` / `--frames-only` 플래그를 적극 활용한다.