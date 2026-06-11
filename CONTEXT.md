소자윤 (글읽는소자윤) - 프로젝트 컨텍스트

## 채널 목적
저작권 만료된 한국 시인의 시를 TTS로 낭송하여 수면/배경음 용도의 롱폼 오디오 콘텐츠를 제작한다.
유튜브 채널명은 글읽는소자윤 (줄여서 소자윤). 보이스는 타입캐스트 소영.

## 파이프라인 구조
1. **위키소스 스크래핑 (`scrape_poems.py`)**
   - `poems/raw/{시인}/` : 원문 txt (구표기 그대로 유지)
   - Claude Sonnet API 호출하여 현대 맞춤법 변환
   - `poems/modern/{시인}/{시인}.json` : 현대 맞춤법 JSON

2. **TTS 생성 (`poem_tts.py`)**
   - gemma3:27b (ollama) : 행별 파라미터(tempo, pitch, pause) 설계
   - 타입캐스트 API : 행별 wav 오디오 생성
   - pydub : 행별 오디오 합치기 및 여백 추가
   - `output/{시인}/audio/` : 개별 시 wav 및 시집 전체 전집 wav
   - `output/{시인}/lines/` : 행별 wav 파일 폴더 및 타임스탬프 JSON, 파라미터 JSON

3. **영상 생성 (`poem_video.py`)**
   - 배경 이미지 (`background.png`) + 타임스탬프 JSON + 시 데이터 조합
   - Pillow 프레임 합성 (좌측: 시 목록 하이라이트 / 우측: 제목 및 본문 페이징)
   - ffmpeg concat + 음원 (전집 wav 반복)
   - `output/{시인}/video/` : 최종 mp4 파일 및 `frames/` 프레임 이미지 폴더

## 시인 관리 원칙
- 저작권 만료 기준: 사망 후 70년 (한국 기준)
- 시인별로 독립적인 폴더 구조를 유지한다.
- 특정 시인/시집에 하드코딩하지 않는다 (항상 `config.json`의 active author/collection을 참조하여 파생 경로 사용).

## 폴더 구조
```text
sojayoonthereader/
├── config.json                 ← 현재 작업 중인 시인 및 대기열 정보
├── poems/
│   ├── raw/{시인}/
│   │   ├── index.json
│   │   └── *.txt               ← 원문 (구표기 그대로)
│   └── modern/{시인}/
│       └── {시인}.json         ← 현대어 변환 완료본
├── output/
│   └── {시인}/
│       ├── audio/              ← 개별 시 wav, 전집 wav
│       ├── lines/              ← params.json, 타임스탬프.json, lines_*/ 폴더들
│       └── video/              ← background.png, 최종 mp4, frames/ 폴더
├── poem_tts.py                 ← TTS 파이프라인 메인
├── scrape_poems.py             ← 스크래핑 + 현대어 변환
├── poem_video.py               ← 영상 생성 (프레임 합성 → ffmpeg → mp4)
└── .env.local                  ← API 키 (git 제외)
```

## 핵심 설정값 (`poem_tts.py`)
- **`config.json` 연동**: 현재 작업 시인 및 시집 이름은 `config.json` 파일의 `active` 항목에서 가져온다.
- **VOICES**: 타입캐스트 보이스 딕셔너리 (소영: `tc_5c789c317ad86500073a02cc`)
- **TTS 모델**: `ssfm-v30`, `emotion_preset`: `tonedown`
- **파라미터 설계**: ollama `gemma3:27b` (로컬)
- **현대어 변환**: Claude Sonnet API (일회성)

## 핵심 설정값 (`poem_video.py`)
- **해상도/FPS**: 1920x1080, 30fps
- **AUDIO_LOOP**: 음원(=영상) 반복 횟수 (기본 3회)
- **FONT_BODY**: 시 본문 폰트 (`GowunDodum-Regular.ttf`)
- **FONT_LIST**: 시 목록 폰트 (`NanumGothic.ttf`)
- **배경 이미지**: `output/{시인}/video/background.png` (필수)
- 한 페이지 표시 행 수(`body_max_lines`)는 본문 영역 높이에서 자동 계산.
- 행 수 초과 시 `_paginate()`로 여러 페이지 분할 (페이지 간 `overlap`행 겹침, 빈 줄 제외 카운트).
- 긴 행의 경우 `_wrap_line()`을 통해 화면 너비(`body_max_w`)에 맞추어 여러 줄로 줄바꿈.

## 현재 상태 (최신 업데이트 기준)
- 김영랑 (영랑시집 53편) 완료.
- 윤동주 (하늘과 바람과 별과 시) 처리 및 오디오/영상 파이프라인 대응 완료.
- `poem_video.py`에서 긴 산문시 형태의 행에 대한 매핑 이슈(0초 분할) 확인 및 대응을 통해 다수 페이지 분할 로직 보완.
- RAG 목적의 reference 관련 기능은 과도한 품이 드는 문제로 현재 당장은 보류(폐기)된 상태임.