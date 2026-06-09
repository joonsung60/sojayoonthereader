"""
사용법: python scrape_poems.py <시인명> <위키소스_목차_URL>
예: python scrape_poems.py 김영랑 https://ko.wikisource.org/wiki/영랑시집
"""

import sys
import os
import json
import re
import time
import requests
import anthropic
from urllib.parse import urlparse, quote, unquote
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(".env.local")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; educational-scraper/1.0)"}

MODERNIZE_SYSTEM_PROMPT = """당신은 한국 고시가 전문가입니다. 1930년대 구한글 표기의 시를 현대 맞춤법으로 변환합니다.

변환 규칙:
1. 구표기를 현대 맞춤법으로 변환 (예: 기둘리고→기다리고, 꼿닙→꽃잎, 잇슬→있을, 아즉→아직, 업서→없어)
2. 본문의 한자는 한글로 변환 (예: 五月→오월, 三百→삼백, 詩→시, 除夜→제야)
3. 제목의 한자 병기 괄호 제거 (예: 불지암(佛地菴)→불지암)
4. 행 구분은 줄바꿈(\\n) 하나, 연 구분은 빈 줄(\\n\\n\\n)로 유지 (\\n\\n은 사용하지 말 것)
5. 원시의 의미와 운율을 최대한 보존

출력 형식: 변환된 시 텍스트만 출력. 원문의 모든 행을 그대로 보존하세요(제목과 동일한 첫 행이 있더라도 절대 제거하지 말 것). 행을 추가/삭제/병합하지 말고, 오직 표기만 현대어로 변환하세요."""

MODERNIZE_USER_TEMPLATE = (
    "다음 시를 현대 맞춤법으로 변환해주세요.\n\n제목: {title}\n\n원문:\n{text}"
)


# ── 유틸 ────────────────────────────────────────────────

def clean_title(title: str) -> str:
    return re.sub(r"\([^)]*[一-鿿][^)]*\)", "", title).strip()


def safe_filename(title: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", title) + ".txt"


# ── 1단계: 스크래핑 ──────────────────────────────────────

def fetch_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_toc_prefix(toc_url: str) -> str:
    parsed = urlparse(toc_url)
    return quote(parsed.path, safe="/") + "/"


def get_poem_links(soup: BeautifulSoup, toc_prefix: str) -> list[dict]:
    content = soup.find("div", class_="mw-parser-output")
    links, seen = [], set()
    for a in content.find_all("a", href=True):
        href = a["href"]
        if href.startswith(toc_prefix) and ":" not in href and href not in seen:
            title = a.get_text(strip=True)
            if title:
                seen.add(href)
                links.append({"title": title, "path": href})
    return links


def extract_poem_text(soup: BeautifulSoup) -> str:
    pages_divs = soup.find_all("div", class_="prp-pages-output")
    if not pages_divs:
        return ""
    # <br> 분류 (HTML 구조 기준):
    #   - 앞에 텍스트가 오는 <br>      → 행 구분(\x01)
    #   - 단독 <br>(앞이 텍스트 아님)  → 연 구분(\x02)  ※ <br><br>의 둘째가 여기 해당
    #   - <p> 열림/닫힘은 무시(페이지 경계)
    # 마커 치환이 다른 <br>의 '앞 판정'을 오염시키지 않도록 역순으로 처리한다.
    # 소스 개행/페이지번호는 아래 토큰화에서 제거하고, 마커는 줄바꿈이 아니라 살아남는다.
    # inline 태그({{Tooltip}}/{{Sic}}의 <span>/<a> 등) 안 텍스트는 get_text("")로 그대로 이어붙음.
    def _preceded_by_text(br) -> bool:
        prev = br.previous_sibling
        while isinstance(prev, str) and not prev.strip():  # 공백 텍스트 노드는 건너뜀
            prev = prev.previous_sibling
        return isinstance(prev, str) and bool(prev.strip())

    tokens = []
    for pages_div in pages_divs:  # 여러 페이지에 걸친 시도 전부 합침
        for br in reversed(pages_div.find_all("br")):
            br.replace_with("\x01" if _preceded_by_text(br) else "\x02")
        for line in pages_div.get_text("").replace("​", "").splitlines():
            stripped = line.strip(" \t\xa0")
            if stripped and not re.fullmatch(r"\d+", stripped):  # 빈 줄·페이지번호 제거
                tokens.append(stripped)

    # 마커 없는 경계(문단/페이지 경계)는 기본 행 구분(\x01)으로 이어붙임
    parts = []
    for tok in tokens:
        parts.append(tok)
        if not tok.endswith(("\x01", "\x02")):
            parts.append("\x01")
    text = "".join(parts)

    # 마커 → 실제 구분자: \x02 = 연 구분(\n\n\n), \x01 = 행 구분(\n)
    text = text.replace("\x02", "\n\n\n").replace("\x01", "\n")
    text = re.sub(r"\n{2,}", "\n\n\n", text)  # 연 구분은 항상 \n\n\n로 정규화
    return text.strip()


def scrape(toc_url: str, raw_dir: Path) -> list[dict]:
    parsed = urlparse(toc_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    toc_prefix = get_toc_prefix(toc_url)

    print(f"목차 로딩: {toc_url}")
    links = get_poem_links(fetch_soup(toc_url), toc_prefix)
    print(f"총 {len(links)}편 발견\n")

    raw_dir.mkdir(parents=True, exist_ok=True)
    index = []

    for i, item in enumerate(links, 1):
        title = item["title"]
        url = base_url + item["path"]
        filename = safe_filename(title)
        print(f"[{i:02d}/{len(links)}] {title} ...", end=" ", flush=True)
        try:
            text = extract_poem_text(fetch_soup(url))
            if not text:
                print("본문 없음 (건너뜀)")
                continue
            (raw_dir / filename).write_text(text, encoding="utf-8")
            index.append({"title": title, "url": url, "filename": filename})
            print(f"저장 ({len(text)}자)")
        except Exception as e:
            print(f"오류: {e}")
        time.sleep(0.5)

    (raw_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n스크래핑 완료: {len(index)}편 → {raw_dir}\n")
    return index


def rescrape_one(title: str, url: str, author: str = "김영랑"):
    """단일 시를 위키소스에서 다시 스크래핑해 raw txt를 덮어쓴다 (테스트/수정용).
    예: rescrape_one("오-매 단풍 들것네",
                     "https://ko.wikisource.org/wiki/영랑시집/오-매_단풍_들것네")"""
    raw_dir = Path("poems/raw") / author
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{title}.txt"

    print(f"재스크래핑: {title}\n  {url}")
    text = extract_poem_text(fetch_soup(url))
    if not text:
        print("  ❌ 본문 없음 (덮어쓰기 취소)")
        return

    out_path.write_text(text, encoding="utf-8")
    print(f"  ✅ 저장: {out_path} ({len(text)}자)")


def debug_page(url: str):
    """URL을 fetch해서 전체 HTML을 저장하고 prp-pages-output div 내용을 출력 (디버그용).
    저장 파일명은 URL 끝의 시 제목에서 추출 → debug_{제목}.html"""
    last = unquote(urlparse(url).path.rstrip("/").split("/")[-1])  # 황홀한_달빛 → 황홀한 달빛
    title = re.sub(r'[\\/*?:"<>|]', "_", last.replace("_", " "))
    out_path = Path("poems/raw/김영랑") / f"debug_{title}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    out_path.write_text(resp.text, encoding="utf-8")
    print(f"✅ 전체 HTML 저장: {out_path} ({len(resp.text)}자)")

    soup = BeautifulSoup(resp.text, "html.parser")
    pages_div = soup.find("div", class_="prp-pages-output")
    print("\n─── prp-pages-output ───")
    if pages_div is None:
        print("❌ prp-pages-output div 없음")
    else:
        print(pages_div.get_text("\n"))


# ── 2단계: 현대어 변환 ────────────────────────────────────

def modernize(index: list[dict], author: str, raw_dir: Path, modern_dir: Path):
    modern_dir.mkdir(parents=True, exist_ok=True)
    output_file = modern_dir / f"{author}.json"
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results = []
    total = len(index)

    print(f"현대어 변환 시작 (claude-sonnet-4-6, {total}편)\n")

    for i, entry in enumerate(index, 1):
        modern_title = clean_title(entry["title"])
        raw_text = (raw_dir / entry["filename"]).read_text(encoding="utf-8").strip()

        print(f"[{i:02d}/{total}] {modern_title:<30}", end=" ", flush=True)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=MODERNIZE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": MODERNIZE_USER_TEMPLATE.format(
                title=modern_title, text=raw_text,
            )}],
        )
        modern_text = response.content[0].text.strip()
        results.append({"title": modern_title, "author": author, "text": modern_text})
        print(f"완료 ({len(modern_text)}자)")

        if i % 10 == 0:
            output_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  → 중간 저장 ({i}편)")

        time.sleep(0.2)

    output_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n변환 완료: {len(results)}편 → {output_file}")


def modernize_one(title: str, author: str = "김영랑"):
    """단일 시 하나만 현대어 변환 (테스트용). raw txt를 읽어 변환 후
    poems/modern/{author}/{author}.json의 같은 제목 항목을 갱신(없으면 추가)."""
    raw_dir = Path("poems/raw") / author
    modern_dir = Path("poems/modern") / author
    modern_dir.mkdir(parents=True, exist_ok=True)
    output_file = modern_dir / f"{author}.json"

    modern_title = clean_title(title)
    raw_path = raw_dir / f"{title}.txt"
    if not raw_path.exists():
        print(f"❌ raw 없음: {raw_path}")
        return
    raw_text = raw_path.read_text(encoding="utf-8").strip()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    print(f"현대어 변환 (단일): {modern_title}")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=MODERNIZE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": MODERNIZE_USER_TEMPLATE.format(
            title=modern_title, text=raw_text,
        )}],
    )
    modern_text = response.content[0].text.strip()
    entry = {"title": modern_title, "author": author, "text": modern_text}

    # 기존 json 갱신 (같은 제목이 있으면 교체, 없으면 추가)
    results = json.loads(output_file.read_text(encoding="utf-8")) if output_file.exists() else []
    for i, e in enumerate(results):
        if e["title"] == modern_title:
            results[i] = entry
            break
    else:
        results.append(entry)
    output_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 변환 완료 ({len(modern_text)}자) → {output_file}\n")
    print(modern_text)
    return entry


def fix_modern_stanza(title: str, author: str = "김영랑"):
    """raw txt의 연 구분 구조를 기준으로 modern json의 text 구분자만 교정.
    Claude 호출 없음, 내용(단어/글자) 변경 없음 — \\n\\n을 \\n으로 바꾸고
    raw의 연 경계에만 \\n\\n\\n을 삽입한다."""
    raw_path = Path("poems/raw") / author / f"{title}.txt"
    modern_file = Path("poems/modern") / author / f"{author}.json"

    if not raw_path.exists():
        print(f"❌ raw 없음: {raw_path}")
        return "no_raw"
    if not modern_file.exists():
        print(f"❌ modern json 없음: {modern_file}")
        return "no_modern"

    # 1) raw에서 연별 본문 행 수 파악 (\n\n\n = 연 구분, \n = 행 구분)
    raw_text = raw_path.read_text(encoding="utf-8").strip()
    stanza_counts = [
        len([ln for ln in stanza.split("\n") if ln.strip()])
        for stanza in raw_text.split("\n\n\n")
    ]
    stanza_counts = [c for c in stanza_counts if c]  # 빈 연 제거

    # 2) modern json에서 해당 시 항목 찾기 (modern 제목은 clean_title 적용본)
    modern_title = clean_title(title)
    data = json.loads(modern_file.read_text(encoding="utf-8"))
    target = next((e for e in data if e["title"] == modern_title), None)
    if target is None:
        print(f"❌ '{modern_title}' 항목이 {modern_file}에 없음")
        return "not_found"

    old_text = target["text"]
    # 구분자 무시하고 본문 행만 순서대로 추출 (내용은 그대로 보존)
    modern_lines = [ln for ln in re.split(r"\n+", old_text.strip()) if ln.strip()]

    # 3) 행 수 검증 — 내용 보존을 위해 raw 본문 행 수와 반드시 일치해야 함
    if len(modern_lines) != sum(stanza_counts):
        print(f"❌ 행 수 불일치: raw 본문 {sum(stanza_counts)}행 vs modern {len(modern_lines)}행")
        print("   (raw에 제목 줄이 따로 있거나 raw가 아직 신규 포맷이 아닐 수 있음) — 중단")
        return "mismatch"

    # 4) raw 연 구조대로 재구성 (행 사이 \n, 연 사이 \n\n\n)
    new_stanzas, cursor = [], 0
    for count in stanza_counts:
        new_stanzas.append("\n".join(modern_lines[cursor:cursor + count]))
        cursor += count
    new_text = "\n\n\n".join(new_stanzas)

    target["text"] = new_text
    modern_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 연 구분 교정 → {modern_file} ({len(stanza_counts)}연 {sum(stanza_counts)}행)\n")
    print("─── BEFORE ───")
    print(old_text)
    print("\n─── AFTER ───")
    print(new_text)
    return "ok"


def fix_all_stanzas(author: str = "김영랑"):
    """index.json의 모든 시를 순회하며 rescrape_one → fix_modern_stanza 일괄 적용.
    행 수 불일치 등으로 교정 못한 시는 마지막에 따로 출력해 수동 확인하게 한다."""
    index_path = Path("poems/raw") / author / "index.json"
    if not index_path.exists():
        print(f"❌ index 없음: {index_path}")
        return

    index = json.loads(index_path.read_text(encoding="utf-8"))
    total = len(index)
    skipped = []  # (title, reason)

    print(f"일괄 연 구분 교정 시작: {total}편 ({author})\n")
    for i, entry in enumerate(index, 1):
        title, url = entry["title"], entry["url"]
        print(f"\n{'='*60}\n[{i:02d}/{total}] {title}\n{'='*60}")
        rescrape_one(title, url, author)               # raw 재스크래핑
        status = fix_modern_stanza(title, author)       # modern 연 구분 교정
        if status != "ok":
            skipped.append((title, status))
        time.sleep(0.5)

    done = total - len(skipped)
    print(f"\n🎉 완료: {done}/{total} 교정, {len(skipped)} 스킵")
    if skipped:
        print("\n⚠️ 수동 확인 필요:")
        for title, reason in skipped:
            print(f"  - {title}  ({reason})")


# ── 메인 ────────────────────────────────────────────────

def main():
    if len(sys.argv) != 3:
        print("사용법: python scrape_poems.py <시인명> <위키소스_목차_URL>")
        print("예: python scrape_poems.py 김영랑 https://ko.wikisource.org/wiki/영랑시집")
        sys.exit(1)

    author, toc_url = sys.argv[1], sys.argv[2]
    raw_dir = Path("poems/raw") / author
    modern_dir = Path("poems/modern") / author

    index = scrape(toc_url, raw_dir)
    if index:
        modernize(index, author, raw_dir, modern_dir)
    else:
        print("스크래핑된 시가 없어 변환을 건너뜁니다.")


if __name__ == "__main__":
    main()
