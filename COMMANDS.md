# 소자윤 명령어 레퍼런스

## 1. 워크플로우 개요 (poem_tts.py)

`poem_tts.py`는 `--mode`에 따라 다음과 같이 동작합니다.

- **`full` (기본값)**: gemma를 이용한 행별 파라미터 설계 + 타입캐스트(Typecast) API를 통한 행별 음원(line wav) 생성 + 오디오 합치기(combine) + 전집 생성
  - 옵션: `--no-collection`을 추가하면 전집 생성 단계를 스킵합니다.
- **`regen`**: 특정 시나 행의 파라미터를 gemma로 재설계하고 타입캐스트 API로 음원(line wav)을 다시 생성합니다. **이 모드는 합치기(combine)를 수행하지 않으므로 변경된 결과를 들으려면 반드시 `rebuild`가 필요합니다.**
- **`rebuild`**: 기존에 생성된 행별 음원(line wav)과 파라미터를 바탕으로 오디오를 다시 합칩니다(combine). 타입캐스트 API를 호출하지 않으며 추가 비용이 발생하지 않습니다.

### 전형적인 작업 흐름 예시

1. **처음 전체 생성** (파라미터 설계 + 녹음 + 합치기 + 전집 생성)
   ```bash
   python poem_tts.py --mode full
   ```
2. **특정 시 재녹음 후 청취** (생성 결과가 마음에 들지 않을 때)
   ```bash
   # 1. 파라미터 재설계 및 재녹음 (합치기 없음)
   python poem_tts.py --mode regen --poems "모란이 피기까지는"

   # 2. 재녹음된 행들을 합쳐서 최종 오디오 생성
   python poem_tts.py --mode rebuild --poems "모란이 피기까지는"
   ```
3. **마음에 드는 params 유지하고 재합성만 수행** (행 간격 등 합치기 로직만 조절/테스트할 때)
   ```bash
   python poem_tts.py --mode rebuild --poems "모란이 피기까지는"
   ```

---

## 2. 저장 경로 (poem_tts.py)

모든 결과물은 `config.json`의 `active.author` 설정에 따라 다음 경로에 저장됩니다.

- **최종 오디오 (audio wav)**: `output/{author}/audio/`
  - 예: `모란이_피기까지는_0611_1234.wav`, `영랑시집_전집_0611_1234.wav`
- **행별 오디오 (line wav)**: `output/{author}/lines/lines_{timestamp}_{title}/`
  - 예: `line_00.wav`
- **TTS 파라미터 (params)**: `output/{author}/params/{title}_{timestamp}_params.json`

---

## 3. 명령어 목록

### poem_tts.py (오디오 생성)

**전체 시 및 전집 생성**
```bash
python poem_tts.py
# 또는 python poem_tts.py --mode full
```

**전체 시 생성 (전집 생성 스킵)**
```bash
python poem_tts.py --mode full --no-collection
```

**특정 시 전체 재녹음** (params.json 재설계 + 타입캐스트 API 호출)
```bash
python poem_tts.py --mode regen --poems "모란이 피기까지는" "황홀한 달빛"
```

**특정 시의 특정 행만 재녹음** (주의: params.json 전체가 새로 생성됨)
```bash
python poem_tts.py --mode regen --poems "모란이 피기까지는" --lines 0 2
```

**전체 재합성 및 전집 갱신** (타입캐스트 호출 없음)
```bash
python poem_tts.py --mode rebuild
```

**특정 시만 재합성** (지정되지 않은 시는 기존 최신 wav를 재사용하여 전집 갱신)
```bash
python poem_tts.py --mode rebuild --poems "모란이 피기까지는"
```

**타임스탬프 생성**
```bash
python poem_tts.py --mode timestamps
```

---

### scrape_poems.py (스크래핑 및 현대어 변환)

> 💡 모든 `scrape_poems.py` 관련 기능은 `author` 인자가 필수입니다.

**전체 스크래핑 + 현대어 변환**
```bash
python scrape_poems.py {시인명} {위키문헌_목차_URL}
예: python scrape_poems.py 윤동주 https://ko.wikisource.org/wiki/하늘과바람과별과시
```

**단일 시 재스크래핑 (raw txt 덮어씀)**
```python
from scrape_poems import rescrape_one
rescrape_one('황홀한 달빛', 'https://ko.wikisource.org/wiki/영랑시집/황홀한_달빛', '김영랑')
```

**전체 시 연/행 구분 교정 (modern JSON)**
```bash
python -c "from scrape_poems import fix_all_stanzas; fix_all_stanzas('김영랑')"
```

**단일 시 연/행 구분 교정**
```python
from scrape_poems import fix_modern_stanza
fix_modern_stanza('오-매 단풍 들것네', '김영랑')
```

**단일 시 현대어 재변환 (Claude API 1회 호출)**
```python
from scrape_poems import modernize_one
modernize_one('황홀한 달빛', '김영랑')
```

**한자 제목 현대어 변환**
```bash
python -c "from scrape_poems import modernize_titles; modernize_titles('김영랑')"
```

**HTML 디버그 (파싱 문제 확인용)**
```python
from scrape_poems import debug_page
debug_page('https://ko.wikisource.org/wiki/영랑시집/황홀한_달빛')
```

---

### poem_video.py (영상 생성)

**프레임 1장 테스트**
```bash
python poem_video.py --test
```

**첫 3편 짧은 영상 테스트**
```bash
python poem_video.py --test-short
```

**전체 프레임 생성**
```bash
python poem_video.py --frames-only
```

**전체 영상 생성 (108분, 음원 3회 반복)**
```bash
python poem_video.py
```

---

### 유지보수

**타입캐스트 보이스 목록 조회**
```bash
python -c "
from dotenv import load_dotenv
import os, requests
load_dotenv('.env.local')
key = os.getenv('TYPECAST_API_KEY')
res = requests.get('https://api.typecast.ai/v1/voices', headers={'X-API-KEY': key})
for v in res.json():
    print(v.get('voice_name',''), '|', v.get('voice_id',''))
" | grep -i "찾는이름"
```

**params.json에서 특정 행 제거**
```python
import json
from pathlib import Path
params_path = Path('output/김영랑/params/{시제목}_{timestamp}_params.json')
params = json.loads(params_path.read_text(encoding='utf-8'))
params.pop(1)  # index 번호
params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding='utf-8')
```

**line wav 파일명 재정렬 (특정 행 삭제 후)**
```bash
cd "output/김영랑/lines/lines_{timestamp}_{시제목}"
for i in $(seq {시작} {끝}); do
    mv "line_0${i}.wav" "line_0$((i-1)).wav"
done
```