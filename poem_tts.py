"""
시 낭송 TTS 파이프라인
Claude API → 행별 파라미터 설계 → 타입캐스트 API → pydub 합치기
"""

import os
import json
import time
import requests
import anthropic
from pathlib import Path
from pydub import AudioSegment
from pydub.effects import normalize
from dotenv import load_dotenv
from datetime import datetime


load_dotenv(".env.local")

TYPECAST_API_KEY = os.getenv("TYPECAST_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
VOICES = {
    "소영": "tc_5c789c317ad86500073a02cc",
}

# ── 시 원문 ──────────────────────────────────────────
POEM_TITLE = "모란이 피기까지는"
POEM_AUTHOR = "김영랑"
POEM_TEXT = """모란이 피기까지는
나는 아직 나의 봄을 기다리고 있을 테요.
모란이 뚝뚝 떨어져 버린 날
나는 비로소 봄을 여읜 설움에 잠길 테요.
오월 어느 날, 그 하루 무덥던 날,
떨어져 누운 꽃잎마저 시들어 버리고는
천지에 모란은 자취도 없어지고
뻗쳐 오르던 내 보람 서운케 무너졌느니,
모란이 지고 말면 그뿐, 내 한 해는 다 가고 말아.
삼백 예순 날 하냥 섭섭해 우옵내다.
모란이 피기까지는
나는 아직 기다리고 있을 테요, 찬란한 슬픔의 봄을."""

# ── 1단계: Claude로 행별 파라미터 설계 ────────────────
def design_poem_params(poem_text: str, title: str, author: str) -> list[dict]:
    print("🤖 Claude가 낭송 파라미터 설계 중...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = """당신은 TTS 파라미터 설계 전문가입니다.
한국 시의 행 구조, 구두점, 연 구분을 분석하여
낭송에 최적화된 파라미터를 설계합니다.
시의 각 행을 분석하여 TTS 낭송을 위한 파라미터를 JSON으로 설계하세요.

각 행마다 다음을 반환하세요.
- line: 원문 텍스트
- tempo: 속도 (0.8 ~ 1.0, 느릴수록 시적 여운)
- audio_pitch: 음높이 (-2, -1, 0 중 하나, 정수만)
- previous_text: 이 행의 앞 행 텍스트 (없으면 빈 문자열)
- next_text: 이 행의 다음 행 텍스트 (없으면 빈 문자열)
- pause_after_ms: 이 행 이후 무음 시간 (ms). 행 사이 800~1500, 연 사이 2500~3500
- note: 이 행의 낭송 의도 (간단히)

빈 줄(\n\n)은 연 구분이며 pause_after_ms를 2500~3500ms로 설정하세요.
빈 줄 없는 행 사이는 800~1500ms로 설정하세요.

반드시 JSON 배열만 반환하세요. 다른 텍스트 없이."""

    user_prompt = f"""제목: {title}
시인: {author}

{poem_text}

위 시의 각 행마다 TTS 파라미터를 설계하세요. 시 텍스트의 구조와 흐름만 보고 판단하세요."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    raw = response.content[0].text.strip()
    # JSON 펜스 제거
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    params = json.loads(raw)
    print(f"✅ {len(params)}개 행 파라미터 설계 완료\n")
    for p in params:
        print(f"  [tempo:{p['tempo']} | pitch:{p['audio_pitch']} | pause:{p['pause_after_ms']}ms] {p['line'][:20]}...")
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

    res = requests.post(url, headers=headers, json=payload)

    if res.status_code != 200:
        print(f"  ❌ 오류 (행 {idx}): {res.status_code} {res.text}")
        return None

    out_path = output_dir / f"line_{idx:02d}.wav"
    with open(out_path, "wb") as f:
        f.write(res.content)

    print(f"  ✅ 행 {idx:02d} 생성: {line_params['line'][:25]}...")
    return out_path


# ── 3단계: pydub로 합치기 ───────────────────────────────
def combine_audio(params: list[dict], audio_files: list[Path], output_path: Path):
    print("\n🎵 오디오 합치는 중...")
    combined = AudioSegment.empty()

    # 시작 전 여백 2초
    combined += AudioSegment.silent(duration=2000)

    for i, (p, audio_file) in enumerate(zip(params, audio_files)):
        if audio_file is None or not audio_file.exists():
            print(f"  ⚠️ 행 {i} 스킵 (파일 없음)")
            continue

        segment = AudioSegment.from_wav(audio_file)
        segment = normalize(segment)  # 행마다 최대 볼륨을 일정하게 맞춤
        combined += segment
        combined += AudioSegment.silent(duration=p.get("pause_after_ms", 1000))

    # 끝 여백 3초
    combined += AudioSegment.silent(duration=3000)

    combined.export(output_path, format="wav")
    duration_sec = len(combined) / 1000
    print(f"✅ 완성: {output_path} ({duration_sec:.1f}초)")


# ── 메인 ────────────────────────────────────────────────
def main():
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%m%d_%H%M")

    title_params = {
        "line": POEM_TITLE,
        "tempo": 0.9,
        "audio_pitch": -1,
        "pause_after_ms": 3000,
        "previous_text": "",
        "next_text": POEM_TEXT.split('\n')[0]
    }

    # Claude 파라미터 설계는 한 번만
    params = [title_params] + design_poem_params(POEM_TEXT, POEM_TITLE, POEM_AUTHOR)

    # 보이스별로 반복
    for voice_name, voice_id in VOICES.items():
        print(f"\n🎤 [{voice_name}] 생성 시작...")
        lines_dir = output_dir / f"lines_{timestamp}_{voice_name}"
        lines_dir.mkdir(exist_ok=True)

        audio_files = []
        for i, p in enumerate(params):
            audio_file = generate_line_audio(p, i, lines_dir, voice_id)
            audio_files.append(audio_file)
            time.sleep(0.5)

        output_path = output_dir / f"{POEM_TITLE}_{voice_name}_{timestamp}.wav"
        combine_audio(params, audio_files, output_path)

    # 파라미터는 한 번만 저장
    params_path = output_dir / f"{POEM_TITLE}_params.json"
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)
    print(f"\n📄 파라미터 저장: {params_path}")

if __name__ == "__main__":
    main()