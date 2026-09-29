"""업로드 영상의 유입 경로(쇼츠 피드·검색·채널 등)를 계정별로 (사람이 요청할 때 로컬 실행).

  python tools/traffic_report.py

YouTube Analytics API (yt-analytics.readonly 권한 필요). 토큰에 권한이 없으면 그 사실만 출력한다.
Analytics 는 2~3일 지연이 있어 최근 영상은 값이 비어 있을 수 있다.
"""
import json
from collections import defaultdict
from datetime import date

from common import ROOT, load_json

NAMES = {"SHORTS": "쇼츠 피드", "YT_SEARCH": "유튜브 검색", "YT_CHANNEL": "채널 페이지", "SUBSCRIBER": "구독 피드",
         "RELATED_VIDEO": "추천 영상", "YT_OTHER_PAGE": "기타 유튜브", "EXT_URL": "외부", "NO_LINK_OTHER": "직접/기타",
         "NOTIFICATION": "알림", "PLAYLIST": "재생목록", "BROWSE": "홈/탐색"}


def main():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    chs = {k: v for k, v in load_json(ROOT / "upload" / "channels.json").items()
           if not k.startswith("_") and v.get("enabled")}
    for ch, cfg in chs.items():
        tp = ROOT / "upload" / "channels" / ch / "token.json"
        tok = json.loads(tp.read_text(encoding="utf-8"))
        print(f"\n## {cfg.get('name', ch)}")
        scopes = tok.get("scopes") or []
        if not any("yt-analytics" in s for s in scopes):
            print(f"  Analytics 권한 없음 (토큰 권한: {scopes})")
            continue
        c = Credentials.from_authorized_user_file(str(tp))
        c.refresh(Request())
        es = [json.loads(l) for l in (ROOT / "upload" / "channels" / ch / "logs" / "uploaded.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [e["video_id"] for e in es if e.get("video_id")]
        if not ids:
            print("  영상 없음")
            continue
        ya = build("youtubeAnalytics", "v2", credentials=c)
        r = ya.reports().query(ids="channel==MINE", startDate="2026-09-26", endDate=date.today().isoformat(),
                               metrics="views", dimensions="insightTrafficSourceType",
                               filters="video==" + ",".join(ids[:200])).execute()
        tot = defaultdict(int)
        for src, v in r.get("rows", []):
            tot[src] += int(v)
        s = sum(tot.values())
        if not s:
            print("  Analytics 데이터 아직 없음(2~3일 지연)")
        for src, v in sorted(tot.items(), key=lambda x: -x[1]):
            print(f"  {NAMES.get(src, src):<10} {v:>6} ({v * 100 // max(s, 1)}%)")


if __name__ == "__main__":
    main()
