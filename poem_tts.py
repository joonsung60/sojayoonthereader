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
_config = json.loads(Path("config.json").read_text(encoding="utf-8"))
AUTHOR = _config["active"]["author"]
COLLECTION = _config["active"]["collection"]
POEMS_FILE = Path(f"poems/modern/{AUTHOR}/{AUTHOR}.json")
AUTHOR_DIR = Path("output") / AUTHOR
AUDIO_DIR = AUTHOR_DIR / "audio"
LINES_DIR = AUTHOR_DIR / "lines"
PARAMS_DIR = AUTHOR_DIR / "params"


def load_poems() -> list[dict]:
    return json.loads(POEMS_FILE.read_text(encoding="utf-8"))


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


# ── 장문 행 내부 분리 (params 생성 단계 전용) ────────────
#   modern json의 text·영상 자막용 원본 행 구조는 절대 건드리지 않는다.
#   여기서 만든 조각은 오직 TTS params/wav 생성에만 쓰인다.
LONG_LINE_MIN_LEN = 100  # 공백 포함 이 길이 이상인 행만 내부 분리 시도
MIN_FRAGMENT_LEN = 5     # 이보다 짧은 조각은 앞(또는 뒤) 조각에 붙인다
SPLIT_SEPARATORS = ("。", ".", "，", ",")  # 마침표·쉼표 모두에서 분리


def _split_long_line(text: str) -> list[str]:
    """행 길이(공백 포함)가 LONG_LINE_MIN_LEN 이상이면 구두점 기준으로 내부 분리한다.

    마침표(。 .)·쉼표(， ,) 모두에서 동시에 끊는다(구분자는 앞 조각 끝에 유지).
    분리 후 5자 미만 조각은 앞 조각(첫 조각이면 다음 조각)에 붙인다.
    분리 대상이 아니면 [원본 행] 한 개만 반환한다.
    """
    if len(text.strip()) < LONG_LINE_MIN_LEN:
        return [text.strip()]

    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in SPLIT_SEPARATORS:
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)
    parts = [p for p in parts if p.strip()]

    if len(parts) < 2:
        return [text.strip()]

    # 5자 미만 조각은 앞 조각에 병합
    merged: list[str] = []
    for part in parts:
        if merged and len(part.strip()) < MIN_FRAGMENT_LEN:
            merged[-1] += part
        else:
            merged.append(part)
    # 첫 조각이 5자 미만이면 다음 조각 앞에 붙인다
    if len(merged) >= 2 and len(merged[0].strip()) < MIN_FRAGMENT_LEN:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)

    merged = [m.strip() for m in merged]
    return merged if len(merged) >= 2 else [text.strip()]


def _inner_pause_ms(fragment: str) -> int:
    """조각 사이 짧은 내부 호흡(700~1000ms). 끝 구두점 강도로 매핑."""
    end = fragment.strip()[-1:] if fragment.strip() else ""
    if end in ("。", "."):
        return 1000
    if end in ("，", ","):
        return 700
    return 800


def _remap_oversplit_params(lines: list[str], gemma_params: list[dict]) -> list[dict] | None:
    """gemma 응답 행 수가 입력보다 많을 때(일부 입력 행을 여러 조각으로 분할한 경우),
    응답의 line 텍스트를 순서대로 이어붙여 입력 행과 대응되는지 확인하고,
    대응되면 입력 행 1개당 params 1개로 재그룹화해 반환한다.
    공백 제거 후 전체가 일치하지 않거나 행 경계가 맞지 않으면 None.
    (재그룹 시 tempo/audio_pitch/note는 첫 조각, pause_after_ms는 마지막 조각 값 사용)"""
    norm = lambda s: re.sub(r"\s+", "", str(s))
    if "".join(norm(gp.get("line", "")) for gp in gemma_params) != "".join(norm(ln) for ln in lines):
        return None
    merged, gi = [], 0
    for ln in lines:
        target, acc, group = norm(ln), "", []
        while gi < len(gemma_params) and len(acc) < len(target):
            acc += norm(gemma_params[gi].get("line", ""))
            group.append(gemma_params[gi])
            gi += 1
        if acc != target or not group:  # 경계 불일치(조각이 행을 가로지름 등)
            return None
        g0, gl = group[0], group[-1]
        m = {"line": ln}
        for k in ("tempo", "audio_pitch", "note"):
            if k in g0:
                m[k] = g0[k]
        if "pause_after_ms" in gl:
            m["pause_after_ms"] = gl["pause_after_ms"]
        merged.append(m)
    if gi != len(gemma_params):  # 남은 조각 있음 → 매핑 실패
        return None
    return merged


# ── 1단계: gemma3:27b로 행별 파라미터 설계 ──────────────
def design_poem_params(poem_text: str, title: str, author: str) -> list[dict]:
    print(f"  🤖 ollama gemma3:27b 파라미터 설계 중...")

    # 1) 코드에서 행을 먼저 분리한다. 연(\n\n\n) 단위로 나눈 뒤 각 연을 행(\n)으로
    #    분리하고 빈 줄은 버린다. 각 행이 연의 마지막 행인지(다음에 연 구분이 오는지) 기록.
    stanzas = poem_text.split("\n\n\n")
    lines: list[str] = []
    stanza_end_idx: set[int] = set()
    for si, stanza in enumerate(stanzas):
        stanza_lines = [ln.strip() for ln in stanza.split("\n") if ln.strip()]
        for li, ln in enumerate(stanza_lines):
            if li == len(stanza_lines) - 1 and si < len(stanzas) - 1:
                stanza_end_idx.add(len(lines))
            lines.append(ln)

    if not lines:
        return []

    # 2) gemma에는 행 번호 + 텍스트를 그대로 넘기고 파라미터만 채우게 한다. 연 구분은 명시.
    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines))
    if stanza_end_idx:
        stanza_note = (
            ", ".join(f"{i}번" for i in sorted(stanza_end_idx))
            + " 행이 연의 마지막 행입니다 (다음에 연 구분이 옵니다)."
        )
    else:
        stanza_note = "연 구분이 없는 한 연짜리 시입니다."

    system_prompt = """당신은 TTS 파라미터 설계 전문가입니다.
'행번호: 텍스트' 형식의 시 행 목록이 주어집니다.
행을 새로 나누거나 합치거나 추가/삭제하지 말고, 주어진 각 행 번호에 대해서만 파라미터를 설계하세요.

각 행마다 다음 필드를 가진 JSON 객체를 반환하세요.
- index: 행 번호 (입력과 동일하게)
- line: 행 텍스트 (입력과 동일하게, 검증용)
- tempo: 속도 (0.8 ~ 1.0, 느릴수록 시적 여운)
- audio_pitch: 음높이 (-1, 0 중 하나, 정수만. -2는 절대로 사용하지 말 것.)
- pause_after_ms: 이 행 이후 무음 시간 (ms). 같은 연 안의 행 사이는 800~1500, 연의 마지막 행은 2500~3500
- note: 이 행의 낭송 의도 (간단히)

연의 마지막 행으로 지정된 행만 pause_after_ms를 2500~3500ms로 설정하고, 나머지는 800~1500ms로 설정하세요.
입력 행 수와 정확히 같은 개수의 객체를 JSON 배열로 반환하세요.
반드시 JSON 배열만 반환하세요. 다른 텍스트 없이."""

    user_prompt = f"""제목: {title}
시인: {author}

행 목록 (행번호: 텍스트):
{numbered}

연 구분 정보: {stanza_note}

위 {len(lines)}개 행 각각에 대해 TTS 파라미터를 설계하세요. 행을 추가하거나 누락하지 마세요."""

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

    gemma_params = json.loads(raw)

    # 3) 응답 행 수 검증: 입력 행 수와 다르면 에러
    #    단, 응답이 더 많고 gemma가 입력 1행을 여러 조각으로 나눈 것이면(line 이어붙여
    #    원본과 일치) 행별로 재그룹화해 정상 처리한다.
    if len(gemma_params) != len(lines):
        remapped = (
            _remap_oversplit_params(lines, gemma_params)
            if len(gemma_params) > len(lines) else None
        )
        if remapped is not None:
            print(f"  ℹ️ gemma 과분할 감지: 응답 {len(gemma_params)}행 → 입력 {len(lines)}행으로 재매핑")
            gemma_params = remapped
        else:
            print(f"  🐞 행 수 불일치 디버그 (입력 {len(lines)}행 / 응답 {len(gemma_params)}행)")
            print("  ── gemma raw 응답 ──")
            print(raw)
            print("  ── 파싱된 params ──")
            print(json.dumps(gemma_params, ensure_ascii=False, indent=2))
            raise ValueError(
                f"gemma 응답 행 수({len(gemma_params)})가 입력 행 수({len(lines)})와 다릅니다."
            )

    # index가 있으면 그 기준으로 정렬 (없으면 입력 순서 가정)
    try:
        gemma_params.sort(key=lambda p: p["index"])
    except (KeyError, TypeError):
        pass

    # 4) line 텍스트·previous/next는 코드가 채우고, gemma 응답의 line은 검증용으로만 사용
    #    장문 행은 여기서만 조각으로 전개한다(원본 lines 구조는 그대로 둠).
    #    - 마지막 조각: 원래 행의 pause_after_ms 유지
    #    - 그 외 조각: 300~500ms 짧은 내부 호흡
    entries: list[dict] = []
    for i, (ln, gp) in enumerate(zip(lines, gemma_params)):
        gemma_line = str(gp.get("line", "")).strip()
        if gemma_line and gemma_line != ln:
            print(f"    ⚠️ 행 {i} 텍스트 불일치: gemma='{gemma_line}' vs 원문='{ln}'")

        tempo = max(0.8, gp.get("tempo", 0.85))
        pitch = int(round(gp.get("audio_pitch", -1)))
        note = gp.get("note", "")
        line_pause = gp.get("pause_after_ms", 3000 if i in stanza_end_idx else 1000)

        fragments = _split_long_line(ln)
        for fi, frag in enumerate(fragments):
            is_last = fi == len(fragments) - 1
            entries.append({
                "line": frag,
                "tempo": tempo,
                "audio_pitch": pitch,
                "pause_after_ms": line_pause if is_last else _inner_pause_ms(frag),
                "note": note,
            })

    # previous_text / next_text는 전개된 실제 인접 조각 기준으로 채운다
    params = []
    for j, e in enumerate(entries):
        params.append({
            "line": e["line"],
            "tempo": e["tempo"],
            "audio_pitch": e["audio_pitch"],
            "previous_text": entries[j - 1]["line"] if j > 0 else "",
            "next_text": entries[j + 1]["line"] if j < len(entries) - 1 else "",
            "pause_after_ms": e["pause_after_ms"],
            "note": e["note"],
        })

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
def build_poem_params(poem: dict, timestamp: str | None = None) -> list[dict]:
    """gemma로 행별 파라미터를 설계하고, 제목 행을 앞에 붙여 PARAMS_DIR에 저장 후 반환.

    저장 파일명은 {safe_title}_{timestamp}_params.json. timestamp 미지정 시 내부 생성.
    """
    title = poem["title"]
    text = poem["text"]
    safe_title = sanitize_filename(title)
    if timestamp is None:
        timestamp = datetime.now().strftime("%m%d_%H%M")

    title_params = {
        "line": title,
        "tempo": 0.9,
        "audio_pitch": -1,
        "pause_after_ms": 3000,
        "previous_text": "",
        "next_text": text.split("\n\n")[0].strip() if text else "",
        "note": "시의 제목. 차분하고 낮은 톤으로, 시 낭송을 시작하기 전 잠시 멈추듯 읽는다.",
    }

    body_params = design_poem_params(text, title, poem["author"])
    all_params = [title_params] + body_params

    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
    params_path = PARAMS_DIR / f"{safe_title}_{timestamp}_params.json"
    params_path.write_text(
        json.dumps(all_params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return all_params


# ── 시 한 편 처리 ────────────────────────────────────────
def process_poem(poem: dict, timestamp: str) -> dict[str, Path]:
    """처리 후 {voice_name: wav_path} 반환"""
    safe_title = sanitize_filename(poem["title"])
    all_params = build_poem_params(poem, timestamp)

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
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
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
def rebuild_collection(poems_filter: list[str] | None = None, make_collection: bool = True):
    """
    기존 line wav들로 개별 wav 재합성 후 전집 생성
    poems_filter: 특정 시만 재합성. None이면 전체
    make_collection: False면 개별 wav만 재합성하고 전집 합치기는 스킵
    전집은 POEMS_FILE 순서대로 (필터 외 시는 기존 최신 wav 재사용)
    """
    poems = load_poems()  # 순서 기준
    timestamp = datetime.now().strftime("%m%d_%H%M")
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
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

        params_files = sorted(PARAMS_DIR.glob(f"{safe_title}_*_params.json"))
        if not params_files:
            print(f"⚠️ {title}: _params.json 없음, 스킵")
            continue

        params = json.loads(params_files[-1].read_text(encoding="utf-8"))
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

    if not make_collection:
        print(f"\n🎉 개별 wav 재합성 완료! {len(collection_wavs)}편 (전집 합치기 스킵)")
        return

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
def main(poems_filter: list[str] | None = None, make_collection: bool = True):
    poems = load_poems()
    if poems_filter is not None:
        poems = [p for p in poems if p["title"] in poems_filter]
    timestamp = datetime.now().strftime("%m%d_%H%M")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    LINES_DIR.mkdir(parents=True, exist_ok=True)
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)

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
    if make_collection:
        for voice_name, wav_paths in collection_paths.items():
            collection_out = AUDIO_DIR / f"{COLLECTION}_전집_{timestamp}.wav"
            combine_collection(wav_paths, collection_out)
        print(f"\n🎉 전체 완료! {len(poems)}편 + 전집 1개")
    else:
        print(f"\n🎉 전체 완료! {len(poems)}편 (전집 합치기 스킵)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "regen", "rebuild", "timestamps"], default="full")
    parser.add_argument("--poems", nargs="*", help="특정 시 제목 리스트")
    parser.add_argument("--lines", nargs="*", type=int, help="재생성할 행 인덱스 리스트")
    parser.add_argument("--no-collection", action="store_true", help="full/rebuild 모드에서 전집 합치기 스킵")
    args = parser.parse_args()

    if args.mode == "full":
        main(poems_filter=args.poems, make_collection=not args.no_collection)
    elif args.mode == "regen":
        regenerate_lines(poems_filter=args.poems, line_indices=args.lines)
    elif args.mode == "rebuild":
        rebuild_collection(poems_filter=args.poems, make_collection=not args.no_collection)
    elif args.mode == "timestamps":
        generate_timestamps()
