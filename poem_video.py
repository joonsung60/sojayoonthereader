"""
시 낭송 영상 생성
배경 + 타임스탬프 + 시 데이터 → 프레임 합성 → ffmpeg → mp4

사용법:
  python poem_video.py          # 전체 영상 생성
  python poem_video.py --test   # 프레임 1개만 생성해서 확인
"""

import json
import subprocess
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from poem_tts import AUTHOR, COLLECTION, AUTHOR_DIR, AUDIO_DIR, LINES_DIR, load_poems


# ── 영상 설정 ──────────────────────────────────────────
VIDEO_W, VIDEO_H = 1920, 1080
FPS = 30
AUDIO_LOOP = 3  # 음원 반복 횟수
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


def _paginate(lines: list[str], max_lines: int, overlap: int = 1) -> list[list[str]]:
    """행 수(빈 줄 제외)가 max_lines를 초과하면 max_lines씩 여러 페이지로 분할.
    연속한 페이지는 overlap행(빈 줄 제외)씩 겹친다. 빈 줄("")은 행 수에 세지 않는다."""
    if sum(1 for ln in lines if ln) <= max_lines:
        return [lines]

    pages: list[list[str]] = []
    start = 0
    while start < len(lines):
        # start부터 비어있지 않은 행을 max_lines개 담을 때까지 진행
        count, end = 0, start
        while end < len(lines) and count < max_lines:
            if lines[end]:
                count += 1
            end += 1
        pages.append(lines[start:end])
        if end >= len(lines):
            break
        # 다음 페이지는 비어있지 않은 행 overlap개만큼 뒤로 물러나서 시작
        back, new_start = 0, end
        while new_start > start and back < overlap:
            new_start -= 1
            if lines[new_start]:
                back += 1
        start = new_start
    return pages


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
    titles = [p["title"] for p in poems]
    text_by_title = {p["title"]: p["text"] for p in poems}
    index_by_title = {t: i for i, t in enumerate(titles)}

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
        for ts in timestamps:
            title = ts["title"]
            if title not in index_by_title:
                continue
            idx = index_by_title[title]
            pages = build_pages(title)
            list_start = _list_window_start(idx, len(titles))
            frame = _compose_frame(bg, titles, idx, list_start, title, pages[0], fonts)
            fp = frames_dir / "test_frame.png"
            frame.save(fp)
            print(f"✅ 테스트 프레임 생성: {fp}  ({title}, 총 {len(pages)}페이지)")
            return
        print("❌ 렌더할 시 없음")
        return

    audio_path = audios[-1] if audios else None
    ts_list = timestamps[:3] if test_short else timestamps
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
    for ts in ts_list:
        title = ts["title"]
        if title not in index_by_title:
            print(f"  ⚠️ {title}: 시 데이터에 없음, 스킵")
            continue

        idx = index_by_title[title]
        dur = (ts["end_ms"] - ts["start_ms"]) / 1000.0
        pages = build_pages(title)
        list_start = _list_window_start(idx, len(titles))

        page_paths = []
        for pno, page in enumerate(pages, 1):
            frame = _compose_frame(bg, titles, idx, list_start, title, page, fonts)
            fp = frames_dir / f"frame_{idx:02d}_p{pno}.png"
            frame.save(fp)
            page_paths.append(fp)
        frame_count += len(page_paths)

        if len(pages) == 1:
            segments.append((page_paths[0], dur))
        else:
            # 절반 지점에서 2페이지로 전환
            segments.append((page_paths[0], dur / 2))
            segments.append((page_paths[1], dur - dur / 2))

        print(f"  ✅ [{idx + 1:02d}] {title} — {len(pages)}페이지")

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
