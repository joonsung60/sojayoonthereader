# 소자윤 명령어 레퍼런스

## scrape_poems.py

### 전체 스크래핑 + 현대어 변환
```bash
python scrape_poems.py {시인명} {위키문헌_목차_URL}
예: python scrape_poems.py 윤동주 https://ko.wikisource.org/wiki/하늘과바람과별과시
```

### 단일 시 재스크래핑 (raw txt 덮어씀)
```python
from scrape_poems import rescrape_one
rescrape_one('황홀한 달빛', 'https://ko.wikisource.org/wiki/영랑시집/황홀한_달빛')
```

### 전체 시 연/행 구분 교정 (modern JSON)
```bash
python3 -c "from scrape_poems import fix_all_stanzas; fix_all_stanzas()"
```

### 단일 시 연/행 구분 교정
```python
from scrape_poems import fix_modern_stanza
fix_modern_stanza('오-매 단풍 들것네')
```

### 단일 시 현대어 재변환 (Claude API 1회 호출)
```python
from scrape_poems import modernize_one
modernize_one('황홀한 달빛')
```

### HTML 디버그 (파싱 문제 확인용)
```python
from scrape_poems import debug_page
debug_page('https://ko.wikisource.org/wiki/영랑시집/황홀한_달빛')
```

---

## poem_tts.py

### 전체 음원 생성 (53편 + 전집)
```bash
python poem_tts.py
```

### 특정 시 재녹음 (params.json 재설계 + 타입캐스트 API)
```bash
python poem_tts.py --mode regen --poems "모란이 피기까지는"
```

### 여러 시 한 번에 재녹음
```bash
python poem_tts.py --mode regen --poems "황홀한 달빛" "언덕에 바로 누워" "밤사람 그립고야"
```

### 특정 행만 재녹음 (주의: params.json 전체 재생성됨)
```bash
python poem_tts.py --mode regen --poems "모란이 피기까지는" --lines 0
```

### 재합성 (타입캐스트 API 없이 line wav → 개별 wav + 전집)
```bash
python poem_tts.py --mode rebuild
```

### 특정 시만 재합성 (나머지는 기존 wav 재사용)
```bash
python poem_tts.py --mode rebuild --poems "모란이 피기까지는"
```

### 타임스탬프 생성
```bash
python poem_tts.py --mode timestamps
```

---

## poem_video.py

### 프레임 1장 테스트
```bash
python poem_video.py --test
```

### 첫 3편 짧은 영상 테스트
```bash
python poem_video.py --test-short
```

### 전체 영상 생성 (108분, 음원 3회 반복)
```bash
python poem_video.py
```

### 프레임 이미지만 생성 (ffmpeg 인코딩 생략)
```bash
python poem_video.py --frames-only
# → output/{시인}/video/frames/ 에 frame_{idx}_p{page}.png 저장
```

---

## 유지보수

### 타입캐스트 보이스 목록 조회
```bash
python3 -c "
from dotenv import load_dotenv
import os, requests
load_dotenv('.env.local')
key = os.getenv('TYPECAST_API_KEY')
res = requests.get('https://api.typecast.ai/v1/voices', headers={'X-API-KEY': key})
for v in res.json():
    print(v.get('voice_name',''), '|', v.get('voice_id',''))
" | grep -i "찾는이름"
```

### params.json에서 특정 행 제거
```python
import json
from pathlib import Path
params_path = Path('output/김영랑/lines/{시제목}_params.json')
params = json.loads(params_path.read_text(encoding='utf-8'))
params.pop(1)  # index 번호
params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding='utf-8')
```

### line wav 파일명 재정렬 (특정 행 삭제 후)
```bash
cd "output/김영랑/lines/lines_{timestamp}_{시제목}"
for i in $(seq {시작} {끝}); do
    mv "line_0${i}.wav" "line_0$((i-1)).wav"
done
```