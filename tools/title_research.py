"""유튜브에서 키워드별 최근 인기 쇼츠 제목 수집 → library/title_trends.json (제목 작성 참고용).

  python tools/title_research.py [--channel channel1_hyeoldang] [--days 30]

- search.list(키워드당 쿼터 100유닛, 최근 N일, 한국, 짧은 영상, 조회수순) → videos.list 로 실제 조회수(1유닛/50개)
- 키워드는 KEYWORDS. 결과는 조회수 상위순 제목 목록(중복 제거).
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from common import ROOT, save_json

KEYWORDS = ["당뇨", "혈당 스파이크", "식후혈당", "혈당 낮추는 법", "혈당 관리"]


def main():
    p = ROOT / "library" / "title_trends.json"
    old = json.loads(p.read_text(encoding="utf-8")).get("updated", "2000-01-01 00:00") if p.exists() else "2000-01-01 00:00"
    if "--force" not in sys.argv and datetime.now() - datetime.strptime(old, "%Y-%m-%d %H:%M") < timedelta(days=3):
        print("title_trends.json 이 3일 이내 → 건너뜀(쿼터 절약)")
        return
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    ch = sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv else "channel1_hyeoldang"
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
    c = Credentials.from_authorized_user_file(str(ROOT / "upload" / "channels" / ch / "token.json"))
    c.refresh(Request())
    yt = build("youtube", "v3", credentials=c)
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    for kw in KEYWORDS:
        r = yt.search().list(part="snippet", q=kw, type="video", order="viewCount", publishedAfter=after,
                             regionCode="KR", relevanceLanguage="ko", videoDuration="short", maxResults=25).execute()
        for it in r.get("items", []):
            found.setdefault(it["id"]["videoId"], {"title": it["snippet"]["title"], "channel": it["snippet"]["channelTitle"],
                                                   "keyword": kw, "published": it["snippet"]["publishedAt"][:10]})
    ids = list(found)
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute().get("items", []):
            found[v["id"]]["views"] = int(v["statistics"].get("viewCount", 0))
    rows = sorted((dict(id=k, **v) for k, v in found.items() if any(w in v["title"] for w in ("혈당", "당뇨"))),
                  key=lambda x: -x.get("views", 0))   # 무관한 영상(고양이·아이돌 등) 제외
    save_json(ROOT / "library" / "title_trends.json",
              {"updated": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"), "days": days,
               "keywords": KEYWORDS, "top": rows[:40]})
    for x in rows[:20]:
        print(f"{x.get('views', 0):>10,} | {x['title']}")


if __name__ == "__main__":
    main()
