"""
시 낭송 TTS 파이프라인
gemma3:27b → 행별 파라미터 설계 → 타입캐스트 API → pydub 합치기
"""

import os
import json
import re
import time
import requests as req
from pathlib import Path
from pydub import AudioSegment
from pydub.effects import normalize
from dotenv import load_dotenv
from datetime import datetime


load_dotenv(".env.local")

TYPECAST_API_KEY = os.getenv("TYPECAST_API_KEY")
VOICES = {
    "소영": "tc_5c789c317ad86500073a02cc",
}

# ── 설정 ──────────────────────────────────────────────
AUTHOR = "김영랑"
COLLECTION = "영랑시집"
POEMS_FILE = Path(f"poems/modern/{AUTHOR}/{AUTHOR}.json")
AUTHOR_DIR = Path("output") / AUTHOR
AUDIO_DIR = AUTHOR_DIR / "audio"
LINES_DIR = AUTHOR_DIR / "lines"


def load_poems() -> list[dict]:
    return json.loads(POEMS_FILE.read_text(encoding="utf-8"))


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


# ── 1단계: gemma3:27b로 행별 파라미터 설계 ──────────────
def design_poem_params(poem_text: str, title: str, author: str) -> list[dict]:
    print(f"  🤖 ollama gemma3:27b 파라미터 설계 중...")

    system_prompt = """당신은 TTS 파라미터 설계 전문가입니다.
한국 시의 행 구조, 구두점, 연 구분을 분석하여
낭송에 최적화된 파라미터를 설계합니다.
시의 각 행을 분석하여 TTS 낭송을 위한 파라미터를 JSON으로 설계하세요.

각 행마다 다음을 반환하세요.
- line: 원문 텍스트
- tempo: 속도 (0.8 ~ 1.0, 느릴수록 시적 여운)
- audio_pitch: 음높이 (-1, 0 중 하나, 정수만. -2는 절대로 사용하지 말 것.)
- previous_text: 이 행의 앞 행 텍스트 (없으면 빈 문자열)
- next_text: 이 행의 다음 행 텍스트 (없으면 빈 문자열)
- pause_after_ms: 이 행 이후 무음 시간 (ms). 행 사이 800~1500, 연 사이 2500~3500
- note: 이 행의 낭송 의도 (간단히)

빈 줄(\n\n\n)은 연 구분입니다. 연이 끝나는 행(다음에 빈 줄이 오는 행)의 pause_after_ms를 2500~3500ms로 설정하세요.
같은 연 안의 행 사이(\n)는 800~1500ms로 설정하세요. (\n\n은 사용하지 않습니다)

반드시 JSON 배열만 반환하세요. 다른 텍스트 없이."""

    user_prompt = f"""제목: {title}
시인: {author}

{poem_text}

위 시의 각 행마다 TTS 파라미터를 설계하세요. 시 텍스트의 구조와 흐름만 보고 판단하세요."""

    response = req.post("http://localhost:11434/api/generate", json={
        "model": "gemma3:27b",
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "stream": False
    })

    raw = response.json()["response"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    params = json.loads(raw)
    print(f"  ✅ {len(params)}개 행 파라미터 완료")
    for p in params:
        print(f"    [tempo:{p['tempo']} | pitch:{p['audio_pitch']} | pause:{p['pause_after_ms']}ms] {p['line'][:20]}...")
    return params


# ── 2단계: 타입캐스트 API로 행별 wav 생성 ───────────────
def generate_line_audio(line_params: dict, idx: int, output_dir: Path, voice_id: str) -> Path:
    url = "https://api.typecast.ai/v1/text-to-speech"
    headers = {
        "X-API-KEY": TYPECAST_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "voice_id": voice_id,
        "text": line_params["line"],
        "model": "ssfm-v30",
        "language": "kor",
        "prompt": {
            "emotion_type": "preset",
            "emotion_preset": "tonedown"
        },
        "output": {
            "volume": 100,
            "audio_pitch": int(round(line_params.get("audio_pitch", -1))),
            "audio_tempo": line_params.get("tempo", 0.85),
            "audio_format": "wav"
        }
    }

    res = req.post(url, headers=headers, json=payload)

    if res.status_code != 200:
        print(f"    ❌ 오류 (행 {idx}): {res.status_code} {res.text}")
        return None

    out_path = output_dir / f"line_{idx:02d}.wav"
    with open(out_path, "wb") as f:
        f.write(res.content)

    print(f"    ✅ 행 {idx:02d}: {line_params['line'][:25]}...")
    return out_path


# ── 3단계: pydub로 합치기 ───────────────────────────────
def combine_audio(params: list[dict], audio_files: list[Path], output_path: Path):
    print("  🎵 오디오 합치는 중...")
    combined = AudioSegment.empty()

    # 시작 전 여백 1초
    combined += AudioSegment.silent(duration=1000)

    for i, (p, audio_file) in enumerate(zip(params, audio_files)):
        if audio_file is None or not audio_file.exists():
            print(f"    ⚠️ 행 {i} 스킵 (파일 없음)")
            continue

        segment = AudioSegment.from_wav(audio_file)
        segment = normalize(segment)
        combined += segment
        combined += AudioSegment.silent(duration=p.get("pause_after_ms", 1000))

    # 끝 여백 2초
    combined += AudioSegment.silent(duration=2000)

    combined.export(output_path, format="wav")
    duration_sec = len(combined) / 1000
    print(f"  ✅ 완성: {output_path} ({duration_sec:.1f}초)")


# ── 파라미터 생성 + 저장 ─────────────────────────────────
def build_poem_params(poem: dict) -> list[dict]:
    """gemma로 행별 파라미터를 설계하고, 제목 행을 앞에 붙여 _params.json에 저장 후 반환."""
    title = poem["title"]
    text = poem["text"]
    safe_title = sanitize_filename(title)

    title_params = {
        "line": f"제목: {title}",
        "tempo": 0.9,
        "audio_pitch": -1,
        "pause_after_ms": 3000,
        "previous_text": "",
        "next_text": text.split("\n\n")[0].strip() if text else "",
    }

    body_params = design_poem_params(text, title, poem["author"])
    all_params = [title_params] + body_params

    params_path = LINES_DIR / f"{safe_title}_params.json"
    params_path.write_text(
        json.dumps(all_params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return all_params


# ── 시 한 편 처리 ────────────────────────────────────────
def process_poem(poem: dict, timestamp: str) -> dict[str, Path]:
    """처리 후 {voice_name: wav_path} 반환"""
    safe_title = sanitize_filename(poem["title"])
    all_params = build_poem_params(poem)

    # 보이스별 오디오 생성
    generated = {}
    for voice_name, voice_id in VOICES.items():
        print(f"\n  🎤 [{voice_name}] 생성 시작...")
        lines_dir = LINES_DIR / f"lines_{timestamp}_{safe_title}"
        lines_dir.mkdir(exist_ok=True)

        audio_files = []
        for i, p in enumerate(all_params):
            audio_file = generate_line_audio(p, i, lines_dir, voice_id)
            audio_files.append(audio_file)
            time.sleep(0.5)

        output_path = AUDIO_DIR / f"{safe_title}_{timestamp}.wav"
        combine_audio(all_params, audio_files, output_path)
        generated[voice_name] = output_path

    return generated


# ── 전집 합치기 ──────────────────────────────────────────
def combine_collection(wav_paths: list[Path], output_path: Path):
    print(f"\n📚 전집 합치는 중... ({len(wav_paths)}편)")
    collection = AudioSegment.empty()
    gap = AudioSegment.silent(duration=0)

    for i, wav_path in enumerate(wav_paths):
        if wav_path is None or not wav_path.exists():
            print(f"  ⚠️ {wav_path} 없음, 스킵")
            continue
        collection += AudioSegment.from_wav(wav_path)
        if i < len(wav_paths) - 1:
            collection += gap

    collection.export(output_path, format="wav")
    duration_min = len(collection) / 1000 / 60
    print(f"✅ 전집 완성: {output_path} ({duration_min:.1f}분)")


# ── 행 재생성 (부분 또는 전체) ───────────────────────────
def regenerate_lines(
    poems_filter: list[str] | None = None,
    line_indices: list[int] | None = None,
):
    """
    poems_filter: 재생성할 시 제목 리스트. None이면 전체
    line_indices: 재생성할 행 인덱스. None이면 해당 시의 전체 행
    gemma로 _params.json을 새로 생성한 뒤, lines_* 폴더 기준으로 Typecast API 호출
    """
    targets = [
        p for p in load_poems()
        if poems_filter is None or p["title"] in poems_filter
    ]
    print(f"재생성 대상: {len(targets)}편\n")

    for poem in targets:
        safe_title = sanitize_filename(poem["title"])
        lines_dirs = sorted(LINES_DIR.glob(f"lines_*_{safe_title}"))

        if not lines_dirs:
            print(f"⚠️ {poem['title']}: lines_* 폴더 없음, 스킵")
            continue

        lines_dir = lines_dirs[-1]  # 가장 최신 폴더

        print(f"📖 {poem['title']}  ({lines_dir.name})")
        params = build_poem_params(poem)  # gemma로 params.json 새로 생성·저장
        indices = line_indices if line_indices is not None else range(len(params))
        for voice_name, voice_id in VOICES.items():
            print(f"  🎤 [{voice_name}]")
            for idx in indices:
                if idx >= len(params):
                    print(f"    ⚠️ 인덱스 {idx} 범위 초과 (총 {len(params)}행), 스킵")
                    continue
                generate_line_audio(params[idx], idx, lines_dir, voice_id)
                time.sleep(0.5)
        print()

    print("재생성 완료")


# ── 재합성 (line wav → 개별 wav + 전집) ─────────────────
def rebuild_collection(poems_filter: list[str] | None = None):
    """
    기존 line wav들로 개별 wav 재합성 후 전집 생성
    poems_filter: 특정 시만 재합성. None이면 전체
    전집은 POEMS_FILE 순서대로 (필터 외 시는 기존 최신 wav 재사용)
    """
    poems = load_poems()  # 순서 기준
    timestamp = datetime.now().strftime("%m%d_%H%M")
    targets_set = set(poems_filter) if poems_filter else None
    collection_wavs: dict[str, Path] = {}  # title → wav_path

    for poem in poems:
        title = poem["title"]
        safe_title = sanitize_filename(title)

        if targets_set and title not in targets_set:
            # 필터 외: 기존 최신 wav 재사용
            existing = sorted(AUDIO_DIR.glob(f"{safe_title}_*.wav"))
            if existing:
                collection_wavs[title] = existing[-1]
            else:
                print(f"⚠️ {title}: 기존 wav 없음, 스킵")
            continue

        params_path = LINES_DIR / f"{safe_title}_params.json"
        if not params_path.exists():
            print(f"⚠️ {title}: _params.json 없음, 스킵")
            continue

        params = json.loads(params_path.read_text(encoding="utf-8"))
        lines_dirs = sorted(LINES_DIR.glob(f"lines_*_{safe_title}"))
        if not lines_dirs:
            print(f"⚠️ {title}: lines_* 폴더 없음, 스킵")
            continue

        lines_dir = lines_dirs[-1]
        audio_files = [lines_dir / f"line_{i:02d}.wav" for i in range(len(params))]
        output_path = AUDIO_DIR / f"{safe_title}_{timestamp}.wav"

        print(f"\n📖 {title}")
        combine_audio(params, audio_files, output_path)
        collection_wavs[title] = output_path

    # 전집: POEMS_FILE 순서대로
    ordered_wavs = [collection_wavs[p["title"]] for p in poems if p["title"] in collection_wavs]
    print(f"\n📚 전집 합치는 중... ({len(ordered_wavs)}편)")
    collection = AudioSegment.empty()

    for wav_path in ordered_wavs:
        if not wav_path.exists():
            print(f"  ⚠️ {wav_path.name} 없음, 스킵")
            continue
        collection += AudioSegment.from_wav(wav_path)

    collection_out = AUDIO_DIR / f"{COLLECTION}_전집_{timestamp}.wav"
    collection.export(collection_out, format="wav")
    duration_min = len(collection) / 1000 / 60
    print(f"✅ 전집 완성: {collection_out} ({duration_min:.1f}분)")


# ── 타임스탬프 생성 ───────────────────────────────────────
def generate_timestamps():
    poems = load_poems()
    timestamps = []
    cursor_ms = 0

    for poem in poems:
        title = poem["title"]
        safe_title = sanitize_filename(title)
        wavs = sorted(AUDIO_DIR.glob(f"{safe_title}_*.wav"))

        if not wavs:
            print(f"⚠️ {title}: wav 없음, 스킵")
            continue

        duration_ms = len(AudioSegment.from_wav(wavs[-1]))
        timestamps.append({
            "title": title,
            "start_ms": cursor_ms,
            "end_ms": cursor_ms + duration_ms,
        })
        cursor_ms += duration_ms

    out_path = LINES_DIR / f"{AUTHOR}_타임스탬프.json"
    out_path.write_text(
        json.dumps(timestamps, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ 타임스탬프 저장: {out_path} ({len(timestamps)}편)")


# ── 메인 ────────────────────────────────────────────────
def main():
    poems = load_poems()
    timestamp = datetime.now().strftime("%m%d_%H%M")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    LINES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"총 {len(poems)}편 처리 시작 ({AUTHOR_DIR}/)\n")

    # voice_name → [wav_path, ...] (시집 순서)
    collection_paths: dict[str, list[Path]] = {v: [] for v in VOICES}

    for i, poem in enumerate(poems, 1):
        print(f"\n[{i:02d}/{len(poems)}] 📖 {poem['title']}")
        try:
            generated = process_poem(poem, timestamp)
            for voice_name, wav_path in generated.items():
                collection_paths[voice_name].append(wav_path)
        except Exception as e:
            print(f"  ❌ 실패: {e}")
            for voice_name in collection_paths:
                collection_paths[voice_name].append(None)
            continue

    # 전집 wav 생성
    for voice_name, wav_paths in collection_paths.items():
        collection_out = AUDIO_DIR / f"{COLLECTION}_전집_{timestamp}.wav"
        combine_collection(wav_paths, collection_out)

    print(f"\n🎉 전체 완료! {len(poems)}편 + 전집 1개")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "regen", "rebuild", "timestamps"], default="full")
    parser.add_argument("--poems", nargs="*", help="특정 시 제목 리스트")
    parser.add_argument("--lines", nargs="*", type=int, help="재생성할 행 인덱스 리스트")
    args = parser.parse_args()

    if args.mode == "full":
        main()
    elif args.mode == "regen":
        regenerate_lines(poems_filter=args.poems, line_indices=args.lines)
    elif args.mode == "rebuild":
        rebuild_collection(poems_filter=args.poems)
    elif args.mode == "timestamps":
        generate_timestamps()
