"""다큐 느낌 실사 스톡 라이브러리 만들기 (Pixabay, 한 번 로컬에서 실행).

  PIXABAY_API_KEY=... python tools/stock_docu.py            # 받기 + 카탈로그
  python tools/stock_docu.py --zip                          # 서버 배포용 zip (GitHub Release 에 올림)

- 영상: 참고영상 소스/스톡_다큐/pixabay_<id>.mp4 (git 제외, 서버는 Release zip 을 받아 캐시)
- 카탈로그: library/sources/stock_docu/catalog.json (라벨은 검색어 표에서 자동 → Claude 토큰 안 씀)
- Pixabay 라이선스: 상업 이용 가능, 출처 표기 불필요
"""
import os
import sys
import zipfile

import requests

from common import LIB, ROOT, load_json, probe, save_json

DEST = ROOT / "참고영상 소스" / "스톡_다큐"
CAT = LIB / "sources" / "stock_docu" / "catalog.json"
PER_QUERY = 2
# (검색어, visual, beat, 한국어 설명)
QUERIES = [
    ("tired man office desk", "ugc_drowsy_after_meal", "problem", "사무실 책상에서 지쳐 있는 남성(실사)"),
    ("sleepy woman afternoon", "ugc_drowsy_after_meal", "problem", "오후에 졸려하는 여성(실사)"),
    ("yawning man", "ugc_drowsy_after_meal", "hook", "하품하는 남성(실사)"),
    ("rubbing cold hands", "ugc_numb_hands", "problem", "차가운 손을 비비는 손(실사)"),
    ("elderly leg pain", "ugc_leg_pain", "problem", "다리를 주무르는 중장년(실사)"),
    ("senior climbing stairs", "ugc_breathless", "problem", "계단 오르며 힘들어하는 중장년(실사)"),
    ("drinking water thirsty", "doc_thirst", "problem", "갈증에 물을 마시는 사람(실사)"),
    ("doctor consultation patient", "doc_consult", "mechanism", "의사와 환자 진료 상담(실사)"),
    ("doctor explaining", "doc_consult", "mechanism", "설명하는 의사(실사)"),
    ("hospital corridor", "doc_hospital", "agitate", "병원 복도(실사)"),
    ("blood test laboratory", "doc_lab", "proof", "혈액 검사 실험실(실사)"),
    ("glucose meter finger", "glucose_test", "problem", "손가락 채혈 혈당 측정(실사)"),
    ("microscope research", "lab_research", "proof", "현미경 연구(실사)"),
    ("scientist laboratory", "lab_research", "proof", "실험실 연구원(실사)"),
    ("red blood cells", "anim_blood_cells", "mechanism", "혈관 속 적혈구 흐름(3D)"),
    ("blood vessel", "anim_blood_cells", "mechanism", "혈관 속 혈액(3D)"),
    ("heart beating anatomy", "anim_heart", "agitate", "뛰는 심장 해부 3D"),
    ("human anatomy 3d", "anim_body", "mechanism", "인체 해부 3D"),
    ("white rice bowl", "doc_rice", "enemy", "흰 쌀밥 한 공기(실사)"),
    ("eating rice chopsticks", "doc_rice", "enemy", "젓가락으로 밥 먹는 장면(실사)"),
    ("noodles eating", "doc_noodles", "enemy", "국수 먹는 장면(실사)"),
    ("bread bakery", "doc_carbs", "enemy", "빵·탄수화물(실사)"),
    ("asian food table", "doc_meal", "usage", "밥상·식탁(실사)"),
    ("family dinner table", "doc_meal", "result", "가족 저녁 식사(실사)"),
    ("vegetable salad", "doc_healthy_food", "usage", "채소 샐러드(실사)"),
    ("cooking kitchen", "doc_meal", "usage", "주방에서 요리(실사)"),
    ("senior walking park", "ugc_active_happy", "result", "공원을 걷는 중장년(실사)"),
    ("elderly couple walking", "ugc_active_happy", "result", "함께 걷는 노부부(실사)"),
    ("morning stretching", "ugc_morning_happy", "result", "아침 스트레칭(실사)"),
    ("happy senior woman", "ugc_happy_hands", "result", "웃는 중년 여성(실사)"),
    ("older man jogging", "ugc_exercise_mat", "result", "조깅하는 중장년 남성(실사)"),
    ("grandparents grandchildren", "doc_family", "result", "손주와 함께하는 조부모(실사)"),
]


def build():
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        sys.exit("PIXABAY_API_KEY 가 필요합니다")
    DEST.mkdir(parents=True, exist_ok=True)
    cat = load_json(CAT) if CAT.exists() else []
    have = {c["stock_id"] for c in cat}
    for q, vis, beat, desc in QUERIES:
        r = requests.get("https://pixabay.com/api/videos/", timeout=60, params={
            "key": key, "q": q, "per_page": 10, "safesearch": "true", "video_type": "film"})
        r.raise_for_status()
        n = 0
        for v in r.json().get("hits", []):
            if n >= PER_QUERY:
                break
            if v["id"] in have or v.get("duration", 0) < 4:
                continue
            f = v["videos"].get("medium") or v["videos"].get("small")
            if not f or not f.get("url"):
                continue
            dst = DEST / f"pixabay_{v['id']}.mp4"
            with requests.get(f["url"], stream=True, timeout=300) as d:
                d.raise_for_status()
                with open(dst, "wb") as fo:
                    for chunk in d.iter_content(1 << 20):
                        fo.write(chunk)
            info = probe(dst)
            if (info["h"] or 0) > (info["w"] or 0):   # 세로 영상 제외
                dst.unlink()
                continue
            cat.append({"idx": len(cat), "path": dst.relative_to(ROOT).as_posix(), "name": dst.name, "kind": "stock",
                        "group": "혈당", "type": "video", "provider": "pixabay", "stock_id": v["id"], "page": v["pageURL"],
                        "w": info["w"], "h": info["h"], "duration": round(info["duration"], 2), "query": q,
                        "labels": {"visual": vis, "beat": beat, "desc": desc, "has_text": False, "quality": 3}})
            have.add(v["id"])
            n += 1
        print(f"{q}: +{n}")
        save_json(CAT, cat)
    print(f"총 {len(cat)}개 → {CAT.relative_to(ROOT)}")


def make_zip():
    out = ROOT / "work" / "stock_docu.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(DEST.glob("*.mp4")):
            z.write(p, p.name)
    print(out, round(out.stat().st_size / 1e6), "MB")


if __name__ == "__main__":
    make_zip() if "--zip" in sys.argv else build()
