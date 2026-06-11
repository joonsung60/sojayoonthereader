import json
import re
from pathlib import Path

AUTHOR = "윤동주"
modern_file = Path(f"poems/modern/{AUTHOR}/{AUTHOR}.json")

def clean_title(title: str) -> str:
    return re.sub(r"\([^)]*[一-鿿][^)]*\)", "", title).strip()

def is_date_or_note(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    # 한글 숫자 날짜: 일구...
    if re.match(r'^일구[삼사오육칠팔구공일이]', line):
        return True
    # 한자 숫자 날짜: 一九...
    if re.match(r'^一九', line):
        return True
    # 아라비아 숫자 날짜: 1934, 1941 등
    if re.match(r'^1[89]\d{2}[•·\.\s]', line) or re.match(r'^1[89]\d{2}$', line):
        return True
    # 장소 부기: ~에서 (20자 이하)
    if line.endswith('에서') and len(line) <= 20:
        return True
    return False

data = json.loads(modern_file.read_text(encoding='utf-8'))
changed = []

for entry in data:
    original = entry['text']
    lines = entry['text'].split('\n')

    # 1. 첫 행이 제목과 동일하면 제거
    modern_title = clean_title(entry['title'])
    if lines and lines[0].strip() == modern_title.strip():
        lines = lines[1:]
        # 제거 후 앞에 남은 빈 줄 정리
        while lines and not lines[0].strip():
            lines = lines[1:]

    # 2. 마지막 행(들) 날짜/부기 제거 (반복)
    while lines and is_date_or_note(lines[-1]):
        lines = lines[:-1]
        # 제거 후 뒤에 남은 빈 줄 정리
        while lines and not lines[-1].strip():
            lines = lines[:-1]

    new_text = '\n'.join(lines)
    if new_text != original:
        entry['text'] = new_text
        changed.append(entry['title'])

modern_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"✅ 처리 완료: {len(changed)}/{len(data)}편 변경")
for t in changed:
    print(f"  - {t}")