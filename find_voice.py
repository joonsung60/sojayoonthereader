from dotenv import load_dotenv
import os
import requests

load_dotenv(".env.local", verbose=True)
TYPECAST_API_KEY = os.getenv("TYPECAST_API_KEY")

# 신규 API (v1)
def get_voices_v1():
    url = "https://api.typecast.ai/v1/voices"
    headers = {
        "X-API-KEY": TYPECAST_API_KEY,
        "Content-Type": "application/json"
    }
    res = requests.get(url, headers=headers)
    print(f"v1 상태코드: {res.status_code}")
    if res.status_code == 200:
        voices = res.json()
        for v in voices:
            name = v.get("voice_name", "")
            vid = v.get("voice_id", "")
            if "서현" in name or "Seohyun" in name or "seohyun" in name.lower():
                print(f"✅ 찾았다! name: {name}, voice_id: {vid}")
        print("\n--- 전체 목록 ---")
        for v in voices:
            print(f"  {v.get('voice_name', '')} | {v.get('voice_id', '')}")
    else:
        print(res.text)

# 구 API (actor 기반)
def get_voices_legacy():
    url = "https://typecast.ai/api/actor"
    headers = {
        "Authorization": f"Bearer {TYPECAST_API_KEY}",
        "Content-Type": "application/json"
    }
    res = requests.get(url, headers=headers)
    print(f"\nlegacy 상태코드: {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        actors = data.get("result", [])
        for a in actors:
            name_ko = a.get("name", {}).get("ko", "")
            name_en = a.get("name", {}).get("en", "")
            aid = a.get("actor_id", "")
            if "서현" in name_ko or "서현" in name_en:
                print(f"✅ 찾았다! ko: {name_ko}, en: {name_en}, actor_id: {aid}")
        print("\n--- 전체 목록 ---")
        for a in actors:
            print(f"  {a.get('name', {}).get('ko', '')} / {a.get('name', {}).get('en', '')} | {a.get('actor_id', '')}")
    else:
        print(res.text)

get_voices_v1()
get_voices_legacy()

print(f"키 확인: {TYPECAST_API_KEY[:10] if TYPECAST_API_KEY else 'None - 못 읽음'}")