소자윤 (글읽는소자윤) - 프로젝트 컨텍스트
채널 목적
저작권 만료된 한국 시인의 시를 TTS로 낭송하여 수면/배경음 용도의 롱폼 오디오 콘텐츠를 제작한다.
유튜브 채널명은 글읽는소자윤 (줄여서 소자윤). 보이스는 타입캐스트 소영.
파이프라인 구조
위키소스 스크래핑 (scrape_poems.py)
  → poems/raw/{시인}/           ← 원문 txt (1930년대 표기)
  → Claude Sonnet 현대어 변환
  → poems/modern/{시인}/{시인}.json   ← 현대 맞춤법 JSON

TTS 생성 (poem_tts.py)
  → gemma3:27b (ollama)         ← 행별 파라미터 설계 (비용 절감)
  → 타입캐스트 API               ← 행별 wav 생성
  → pydub 합치기
  → output/{시인}/audio/        ← 개별 시 wav + 전집 wav
  → output/{시인}/lines/        ← params.json + 행별 wav + 타임스탬프

영상 생성 (poem_video.py)
  → 배경 png + 타임스탬프 + 시 데이터
  → Pillow 프레임 합성 (좌: 시 목록 / 우: 제목+본문)
  → ffmpeg concat + 음원 (전집 wav 3회 반복)
  → output/{시인}/video/        ← 최종 mp4
  → output/{시인}/video/frames/ ← 프레임 png (--frames-only 시)
시인 관리 원칙

저작권 만료 기준: 사망 후 70년 (한국 기준)
현재 작업: 김영랑 (1950년 사망, 영랑시집 53편)
예정: 한용운, 윤동주, 이상, 김소월 등
시인별로 독립적인 폴더 구조를 유지한다
특정 시인/시집에 하드코딩하지 않는다 → AUTHOR 상수 하나만 바꾸면 다른 시인으로 전환 가능해야 한다

폴더 구조
sojayoonthereader/
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
핵심 설정값 (poem_tts.py)

AUTHOR - 현재 작업 시인 (예: 김영랑)
COLLECTION - 현재 작업 시집 (전집 파일명에 사용, 예: 영랑시집)
VOICES - 타입캐스트 보이스 딕셔너리 (소영: tc_5c789c317ad86500073a02cc)
TTS 모델: ssfm-v30, emotion_preset: tonedown
파라미터 설계: ollama gemma3:27b (로컬)
현대어 변환: Claude Sonnet API (일회성)

핵심 설정값 (poem_video.py)

해상도/FPS: 1920x1080, 30fps
AUDIO_LOOP - 음원(=영상) 반복 횟수 (기본 3회)
FONT_BODY - 시 본문 폰트 (고운돋움), FONT_LIST - 목록 폰트 (나눔고딕)
배경 이미지: output/{시인}/video/background.png (필수)
한 페이지 행 수(max_lines)는 본문 영역 높이에서 자동 계산, 초과 시 _paginate()로 여러 페이지 분할 (페이지 간 overlap행 겹침, 빈 줄 제외 카운트)
poem_tts.py의 상수/함수를 import (단방향 의존)

실행 방법
```bash
# 새 시인 추가
python scrape_poems.py 윤동주 https://ko.wikisource.org/wiki/하늘과바람과별과시

# 전체 음원 생성
python poem_tts.py

# 특정 행만 재생성
python poem_tts.py --mode regen --poems "모란이 피기까지는" --lines 0

# 전집 재합성
python poem_tts.py --mode rebuild

# 타임스탬프 생성
python poem_tts.py --mode timestamps

# 영상 프레임 1장 테스트
python poem_video.py --test

# 전체 영상 생성
python poem_video.py

# 프레임만 생성 (ffmpeg 인코딩 생략)
python poem_video.py --frames-only
```
현재 상태 (2026-06-10 기준)

김영랑 영랑시집 53편 음원 완성
전집 wav (약 36분) + 타임스탬프 JSON 완성
poem_video.py 영상 생성 파이프라인 구현 완료 (프레임 합성 + ffmpeg)
배경 이미지(background.png) 준비 후 전체 영상 렌더링 단계