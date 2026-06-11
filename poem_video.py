"""
시 낭송 영상 생성
배경 + 타임스탬프 + 시 데이터 → 프레임 합성 → ffmpeg → mp4

사용법:
  python poem_video.py          # 전체 영상 생성
  python poem_video.py --test   # 프레임 1개만 생성해서 확인
"""

import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from pydub import AudioSegment

from poem_tts import AUTHOR_DIR, AUDIO_DIR, LINES_DIR, PARAMS_DIR, load_poems, sanitize_filename

_config = json.loads(Path("config.json").read_text(encoding="utf-8"))
AUTHOR = _config["active"]["author"]
COLLECTION = _config["active"]["collection"]


# ── 영상 설정 ──────────────────────────────────────────
VIDEO_W, VIDEO_H = 1920, 1080
FPS = 30
AUDIO_LOOP = 2  # 음원 반복 횟수
FONT_BODY = "/usr/share/fonts/truetype/gowun/GowunDodum-Regular.ttf"  # 시 본문
FONT_LIST = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"  # 시 목록
LIST_X0, LIST_X1 = 0, 400        # 왼쪽 목록 영역
BODY_X0, BODY_X1 = 450, 1870     # 오른쪽 본문 영역
LIST_WINDOW = 15                 # 목록에 한 번에 보일 시 개수
LIST_STEP = 10                   # 뷰포트 이동 단위
BODY_LINE_H = 60                 # 본문 행 높이(px)


# ── 헬퍼 ────────────────────────────────────────────────
def _wrap_line(text: str, font, max_width: int) -> list[str]:
    """한 행이 max_width를 넘으면 글자 단위로 줄바꿈. 빈 행은 ['']로 보존."""
    if not text:
        return [""]
    out, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) <= max_width:
            cur += ch
        else:
            out.append(cur)
            cur = ch
    out.append(cur)
    return out


def _truncate(text: str, font, max_width: int) -> str:
    """max_width를 넘으면 …로 잘라냄 (목록용)."""
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def _uniquify_titles(names: list[str]) -> list[str]:
    """중복 제목에 고유 내부 키 부여: 첫 등장은 원본, 이후는 제목_2, 제목_3 ...
    등장 순서 기준이므로 poems와 타임스탬프에 동일 적용하면 같은 키로 맞춰진다."""
    seen, keys = {}, []
    for nm in names:
        seen[nm] = seen.get(nm, 0) + 1
        keys.append(nm if seen[nm] == 1 else f"{nm}_{seen[nm]}")
    return keys


def _paginate(lines: list[str], max_lines: int, overlap: int = 1) -> list[list[str]]:
    """실제 렌더링되는 모든 행(빈 줄 포함)이 페이지당 max_lines를 넘지 않게 분할.
    빈 줄("")도 렌더 시 BODY_LINE_H를 차지하므로 행 수에 포함해 센다.
    연속한 페이지는 비어있지 않은 행 overlap개씩 겹치고,
    페이지 시작/끝의 빈 줄(연 구분)은 버려 슬롯 낭비와 어색한 여백을 막는다."""
    def _trim(seg: list[str]) -> list[str]:
        a, b = 0, len(seg)
        while a < b and not seg[a]:
            a += 1
        while b > a and not seg[b - 1]:
            b -= 1
        return seg[a:b]

    if len(_trim(lines)) <= max_lines:
        return [_trim(lines)]

    pages: list[list[str]] = []
    n, start = len(lines), 0
    while start < n:
        while start < n and not lines[start]:  # 페이지가 빈 줄로 시작하지 않게
            start += 1
        if start >= n:
            break
        end = min(start + max_lines, n)        # 빈 줄 포함 max_lines행 슬라이스
        pages.append(_trim(lines[start:end]))
        if end >= n:
            break
        # 다음 페이지는 비어있지 않은 행 overlap개만큼 뒤로 물러나서 시작
        back, new_start = 0, end
        while new_start > start and back < overlap:
            new_start -= 1
            if lines[new_start]:
                back += 1
        start = new_start if new_start > start else end  # 진행 보장
    return pages


def _page_durations(
    title: str, text: str, pages: list[list[str]], total_dur: float,
    body_font, body_max_w: int,
) -> list[float] | None:
    """2페이지 이상 시, 각 페이지의 행을 line wav 길이 + pause_after_ms로 환산해
    페이지별 재생시간(초)을 배분한다(dur/2 균등분할 대신).

    원리: display 행과 params(=line wav)는 모두 같은 '원본 시 행' 순서에서 파생되므로
    원본 행 단위로 정렬한다. params는 PARAMS_DIR 최신 파일, wav는 최신 lines_* 폴더 사용.
    페이지별 마지막 원본 행까지의 누적 오디오 시각을 경계로 삼고, 마지막 페이지는 남은
    전부(말미 여백 포함)를 가져간다. 파일·텍스트 불일치 등 매핑 실패 시 None(→ 폴백)."""
    safe = sanitize_filename(title)
    pfs = sorted(PARAMS_DIR.glob(f"{safe}_*_params.json"))
    ldirs = sorted(LINES_DIR.glob(f"lines_*_{safe}"))
    if not pfs or not ldirs:
        return None
    params = json.loads(pfs[-1].read_text(encoding="utf-8"))
    ldir = ldirs[-1]

    # line wav 길이(ms): params 인덱스 = line_{idx:02d}.wav (0=제목)
    wav_ms = []
    for j in range(len(params)):
        w = ldir / f"line_{j:02d}.wav"
        if not w.exists():
            return None
        wav_ms.append(len(AudioSegment.from_wav(w)))

    # 원본 시 행 (poem_tts.design_poem_params와 동일한 분리)
    orig = [ln.strip() for st in text.split("\n\n\n") for ln in st.split("\n") if ln.strip()]
    if not orig:
        return None

    # 원본 행별 누적 display 행수(빈 줄 제외) — 페이지 경계 → 원본 행 매핑용
    wrap_cum, c = [], 0
    for o in orig:
        c += len(_wrap_line(o, body_font, body_max_w))
        wrap_cum.append(c)

    # body params(=params[1:])를 원본 행별로 묶어 행별 오디오(ms) 합산
    #   (장문 행이 여러 조각으로 나뉘어 params가 더 많을 수 있음 → 텍스트로 그룹화)
    ws = lambda s: re.sub(r"\s+", "", str(s))
    body, bi, orig_audio = params[1:], 0, []
    for o in orig:
        target, acc, start = ws(o), "", bi
        while bi < len(body) and len(acc) < len(target):
            acc += ws(body[bi].get("line", ""))
            bi += 1
        if acc != target:                       # 텍스트 경계 불일치 → 폴백
            return None
        orig_audio.append(sum(
            wav_ms[1 + k] + body[k].get("pause_after_ms", 1000) for k in range(start, bi)
        ))
    if bi != len(body):
        return None

    # 원본 행 i까지 끝난 시각(ms): 선행 1초 여백 + 제목(wav+pause) + 본문 누적
    base = 1000 + wav_ms[0] + params[0].get("pause_after_ms", 1000)
    cum_ms, c = [], base
    for a in orig_audio:
        c += a
        cum_ms.append(c)

    # 페이지 i의 마지막 비어있지 않은 행 ordinal (overlap=1: 페이지마다 1행 겹침)
    ns = [sum(1 for ln in pg if ln) for pg in pages]
    durs, prev = [], 0.0
    for i in range(len(pages)):
        if i < len(pages) - 1:
            ordinal = sum(ns[: i + 1]) - i
            k = next((idx for idx, cc in enumerate(wrap_cum) if cc >= ordinal), len(orig) - 1)
            # 원본 행 k가 여러 페이지에 걸치면(산문시처럼 긴 단일 행이 wrap), 그 행의
            # 오디오를 소비한 wrap 줄 비율만큼만 안분해 경계 시각을 잡는다.
            prev_cum = wrap_cum[k - 1] if k > 0 else 0      # 행 k 직전까지 누적 display 줄
            wrap_k = wrap_cum[k] - prev_cum                  # 행 k의 wrap된 display 줄 수
            frac = min(1.0, (ordinal - prev_cum) / wrap_k) if wrap_k else 1.0
            boundary = (cum_ms[k] - (1.0 - frac) * orig_audio[k]) / 1000.0
            durs.append(boundary - prev)
            prev = boundary
        else:
            durs.append(total_dur - prev)       # 마지막 페이지: 남은 전부
    if any(d <= 0 for d in durs):               # 경계 역전 등 비정상 → 폴백
        return None
    return durs


def _list_window_start(idx: int, n: int) -> int:
    """현재 시(idx)를 보여주기 위한 목록 뷰포트 시작 인덱스.
    10편 단위로 이동: 1-15 → 10-25 → 20-35 → 30-45 → 38-53"""
    if n <= LIST_WINDOW:
        return 0
    which = idx // LIST_STEP
    start = which * LIST_STEP
    if which > 0:
        start -= 1
    return max(0, min(start, n - LIST_WINDOW))


def _compose_frame(
    bg: Image.Image,
    titles: list[str],
    current_idx: int,
    list_start: int,
    title_text: str,
    page_lines: list[str],
    fonts: dict,
) -> Image.Image:
    """배경 + 반투명 오버레이 + 목록/본문 텍스트로 한 프레임 합성."""
    base = bg.copy()

    # 반투명 검은 오버레이 (좌: 목록 / 우: 본문)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([LIST_X0, 0, LIST_X1, VIDEO_H], fill=(0, 0, 0, 150))
    od.rectangle([BODY_X0, 60, BODY_X1, VIDEO_H - 60], fill=(0, 0, 0, 120))
    base = Image.alpha_composite(base, overlay)

    # 텍스트 레이어
    tl = Image.new("RGBA", base.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tl)

    # 왼쪽: 시 목록 (현재=노랑, 나머지=흰색 반투명)
    font_list = fonts["list"]
    y = 70
    for i in range(list_start, min(list_start + LIST_WINDOW, len(titles))):
        color = (255, 221, 0, 255) if i == current_idx else (240, 240, 240, 170)
        label = _truncate(f"{i + 1:02d}. {titles[i]}", font_list, LIST_X1 - 50)
        td.text((30, y), label, font=font_list, fill=color)
        y += 58

    # 오른쪽: 제목 + 본문 (현재 페이지)
    tx = BODY_X0 + 60
    td.text((tx, 110), title_text, font=fonts["title"], fill=(255, 255, 255, 255))
    by = 280
    for ln in page_lines:
        td.text((tx, by), ln, font=fonts["body"], fill=(255, 255, 255, 255))
        by += BODY_LINE_H

    base = Image.alpha_composite(base, tl)
    return base.convert("RGB")


# ── 영상 생성 ────────────────────────────────────────────
def generate_video(test: bool = False, test_short: bool = False, frames_only: bool = False):
    """배경 + 타임스탬프 + 시 데이터로 낭송 영상(mp4) 생성.
    시가 바뀔 때만 프레임을 만들고(시당 최대 2장), ffmpeg으로 이어붙인다.
    test=True면 첫 시의 프레임 1개만 생성하고 종료.
    test_short=True면 첫 3편만 처리해 짧은 mp4 생성(음원도 그 3편 구간만, 반복 없음).
    frames_only=True면 프레임 이미지만 frames_dir에 생성하고 ffmpeg 인코딩은 건너뛴다."""
    video_dir = AUTHOR_DIR / "video"
    frames_dir = video_dir / "frames"
    bg_path = video_dir / "background.png"
    ts_path = LINES_DIR / f"{AUTHOR}_타임스탬프.json"

    # 입력 검증
    if not bg_path.exists():
        print(f"❌ 배경 이미지 없음: {bg_path}")
        return
    if not ts_path.exists():
        print(f"❌ 타임스탬프 없음: {ts_path} (먼저 poem_tts.py --mode timestamps 실행)")
        return
    audios = sorted(AUDIO_DIR.glob(f"{COLLECTION}_전집_*.wav"))
    if not audios and not test and not frames_only:
        print(f"❌ 전집 음원 없음: {AUDIO_DIR}/{COLLECTION}_전집_*.wav")
        return

    frames_dir.mkdir(parents=True, exist_ok=True)
    timestamps = json.loads(ts_path.read_text(encoding="utf-8"))
    poems = load_poems()
    display_titles = [p["title"] for p in poems]            # 목록 표시용(원본 제목)
    titles = _uniquify_titles(display_titles)               # 내부 키(중복 시 제목_2 ...)
    text_by_title = {k: p["text"] for k, p in zip(titles, poems)}
    index_by_title = {k: i for i, k in enumerate(titles)}
    ts_keys = _uniquify_titles([t["title"] for t in timestamps])  # 타임스탬프도 동일 키

    bg = Image.open(bg_path).convert("RGBA").resize((VIDEO_W, VIDEO_H))
    fonts = {
        "list": ImageFont.truetype(FONT_LIST, 26),
        "title": ImageFont.truetype(FONT_BODY, 80),
        "body": ImageFont.truetype(FONT_BODY, 42),
    }

    body_max_w = BODY_X1 - (BODY_X0 + 60) - 40
    body_top, body_bottom = 280, VIDEO_H - 160
    max_lines = (body_bottom - body_top) // BODY_LINE_H

    def build_pages(title: str) -> list[list[str]]:
        # \n\n\n = 연 구분, \n = 행 구분
        # 연 사이엔 빈 줄 1개를 넣어 간격을 행 간격(BODY_LINE_H)의 2배로 벌림 (>=1.5배)
        display: list[str] = []
        for s, stanza in enumerate(text_by_title[title].split("\n\n\n")):
            if s > 0:
                display.append("")  # 연 사이 추가 여백
            for ln in stanza.split("\n"):
                display.extend(_wrap_line(ln, fonts["body"], body_max_w))
        return _paginate(display, max_lines)

    # ── 테스트: 첫 시의 첫 페이지만 1장 생성 ──
    if test:
        for ts, key in zip(timestamps, ts_keys):
            if key not in index_by_title:
                continue
            idx = index_by_title[key]
            pages = build_pages(key)
            list_start = _list_window_start(idx, len(titles))
            frame = _compose_frame(bg, display_titles, idx, list_start, ts["title"], pages[0], fonts)
            fp = frames_dir / "test_frame.png"
            frame.save(fp)
            print(f"✅ 테스트 프레임 생성: {fp}  ({ts['title']}, 총 {len(pages)}페이지)")
            return
        print("❌ 렌더할 시 없음")
        return

    audio_path = audios[-1] if audios else None
    ts_list = timestamps[:3] if test_short else timestamps
    ts_key_list = ts_keys[:3] if test_short else ts_keys
    if frames_only:
        mode_tag = "  [frames-only]"
    elif test_short:
        mode_tag = "  [test-short: 첫 3편]"
    else:
        mode_tag = ""
    print(f"🎬 영상 생성 시작 — 배경: {bg_path.name}, "
          f"음원: {audio_path.name if audio_path else '(없음)'}{mode_tag}")

    # ── 프레임 생성 + 세그먼트(프레임, 길이초) 구성 ──
    segments: list[tuple[Path, float]] = []
    frame_count = 0
    for ts, key in zip(ts_list, ts_key_list):
        if key not in index_by_title:
            print(f"  ⚠️ {ts['title']}: 시 데이터에 없음, 스킵")
            continue

        idx = index_by_title[key]
        dur = (ts["end_ms"] - ts["start_ms"]) / 1000.0
        pages = build_pages(key)
        list_start = _list_window_start(idx, len(titles))

        page_paths = []
        for pno, page in enumerate(pages, 1):
            frame = _compose_frame(bg, display_titles, idx, list_start, ts["title"], page, fonts)
            fp = frames_dir / f"frame_{idx:02d}_p{pno}.png"
            frame.save(fp)
            page_paths.append(fp)
        frame_count += len(page_paths)

        if len(pages) == 1:
            segments.append((page_paths[0], dur))
        else:
            # 페이지 전환 시점을 line wav 길이(+pause) 합산으로 계산해 배분
            # 파일(params/lines wav)은 원본 제목으로 저장되므로 ts["title"]로 조회
            durs = _page_durations(ts["title"], text_by_title[key], pages, dur,
                                   fonts["body"], body_max_w)
            if durs is None:
                durs = [dur / len(pages)] * len(pages)  # 매핑 실패 → 균등 분할 폴백
                print(f"     ⚠️ line wav 매핑 실패 → {len(pages)}페이지 균등 분할")
            for path, d in zip(page_paths, durs):
                segments.append((path, d))

        print(f"  ✅ [{idx + 1:02d}] {ts['title']} — {len(pages)}페이지")

    if not segments:
        print("❌ 생성된 프레임 없음")
        return

    if frames_only:
        print(f"\n🖼️ 프레임만 생성 완료 — {frame_count}장 → {frames_dir}")
        return

    # ── ffmpeg concat 리스트 (음원 반복 횟수만큼 영상도 반복) ──
    loop = 1 if test_short else AUDIO_LOOP  # test-short는 반복 없이 1회
    full = segments * loop
    concat_path = video_dir / "frames.txt"
    lines_out = []
    for fp, d in full:
        lines_out.append(f"file '{fp.resolve()}'")
        lines_out.append(f"duration {d:.3f}")
    lines_out.append(f"file '{full[-1][0].resolve()}'")  # 마지막 프레임 길이 확정용
    concat_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")

    # ── ffmpeg 인코딩 (영상 concat + 음원) ──
    timestamp = datetime.now().strftime("%m%d_%H%M")
    if test_short:
        out_path = video_dir / f"{COLLECTION}_test_short_{timestamp}.mp4"
        # 음원은 첫 3편 구간만: 시작 지점부터 세그먼트 총 길이만큼 잘라 사용 (반복 없음)
        start_ms = ts_list[0]["start_ms"]
        total_sec = sum(d for _, d in segments)
        audio_input = ["-ss", f"{start_ms / 1000:.3f}", "-t", f"{total_sec:.3f}", "-i", str(audio_path)]
    else:
        out_path = video_dir / f"{COLLECTION}_{timestamp}.mp4"
        audio_input = ["-stream_loop", str(AUDIO_LOOP - 1), "-i", str(audio_path)]

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        *audio_input,
        "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    print(f"\n🎞️ ffmpeg 인코딩 중... ({len(segments)}세그먼트 × {loop}회)")
    subprocess.run(cmd, check=True)
    print(f"✅ 영상 완성: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="프레임 1개만 생성해서 확인")
    parser.add_argument("--test-short", action="store_true", help="첫 3편만 짧은 mp4로 생성")
    parser.add_argument("--frames-only", action="store_true", help="프레임 이미지만 생성하고 ffmpeg 인코딩은 건너뜀")
    args = parser.parse_args()
    generate_video(test=args.test, test_short=args.test_short, frames_only=args.frames_only)
