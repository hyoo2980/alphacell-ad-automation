"""유튜브 인기 쇼츠 수집 → library/title_trends.json (주제·훅·제목 참고용, 매일).

  python tools/title_research.py [--channel channel1_hyeoldang] [--force]

- 검색어 12개 × search.list(쇼츠 길이, 최근 90일, 한국, 조회수순) → videos.list 로 조회수·길이
- 3분 이하만 남기고, 하루 평균 조회수(조회수 ÷ 업로드 후 일수) 상위 30개 = "지금 뜨는 것", 누적 조회수 상위 30개
- 같은 날 이미 수집했으면 건너뜀(쿼터 절약, 검색어당 100유닛). 실패해도 제작은 계속(이전 목록 사용)
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from common import ROOT, save_json

KEYWORDS = ["혈당", "혈당 스파이크", "식후 혈당", "혈당 낮추는 음식", "혈당 낮추는 방법", "당뇨 초기증상",
            "당뇨에 좋은 음식", "공복혈당", "인슐린 저항성", "당뇨 전단계", "혈관 건강", "중년 건강 습관"]
KST = timezone(timedelta(hours=9))


def secs(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return sum(int(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1))) if m else 0


def main():
    p = ROOT / "library" / "title_trends.json"
    old = json.loads(p.read_text(encoding="utf-8")).get("updated", "2000-01-01") if p.exists() else "2000-01-01"
    if "--force" not in sys.argv and old[:10] == datetime.now(KST).strftime("%Y-%m-%d"):
        print("오늘 이미 수집함 → 건너뜀")
        return
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    ch = sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv else "channel1_hyeoldang"
    c = Credentials.from_authorized_user_file(str(ROOT / "upload" / "channels" / ch / "token.json"))
    c.refresh(Request())
    yt = build("youtube", "v3", credentials=c)
    now = datetime.now(timezone.utc)
    after = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    for kw in KEYWORDS:
        try:
            r = yt.search().list(part="snippet", q=kw, type="video", order="viewCount", publishedAfter=after,
                                 regionCode="KR", relevanceLanguage="ko", videoDuration="short", maxResults=25).execute()
        except Exception as e:
            print(f"  검색 실패 {kw}: {e}")
            continue
        for it in r.get("items", []):
            found.setdefault(it["id"]["videoId"], {"keyword": kw})
    ids = list(found)
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics,contentDetails",
                                  id=",".join(ids[i:i + 50])).execute().get("items", []):
            pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            days = max((now - pub).total_seconds() / 86400, 0.5)
            views = int(v["statistics"].get("viewCount", 0))
            found[v["id"]].update(title=v["snippet"]["title"], channel=v["snippet"]["channelTitle"],
                                  published=pub.strftime("%Y-%m-%d"), seconds=secs(v["contentDetails"]["duration"]),
                                  views=views, views_per_day=round(views / days))
    rows = [dict(id=k, **v) for k, v in found.items() if v.get("title") and v.get("seconds", 999) <= 180]
    hot = sorted(rows, key=lambda x: -x["views_per_day"])[:30]
    save_json(p, {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "keywords": KEYWORDS,
                  "hot_by_views_per_day": hot, "top_by_views": sorted(rows, key=lambda x: -x["views"])[:30]})
    for x in hot[:15]:
        print(f"{x['views_per_day']:>8,}/일 {x['views']:>10,} | {x['title'][:60]}")


if __name__ == "__main__":
    main()
