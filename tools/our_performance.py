"""우리 영상 성적 → library/our_performance.json (대본 작성 보조 자료, 매일).

- 최근 7일 업로드 중 공개 후 18시간 이상 지난 영상만
- 채널마다 노출 차이가 커서 조회수 그대로가 아니라 "같은 채널 중앙값 대비 몇 배"로 순위
- 상위 5 / 하위 5 + 형식(docad/info/ad)별 평균. 영상마다 제목·주제·헤드라인·첫 훅 대사
조회수는 Data API videos.list(statistics) — 50개당 1유닛.
"""
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ROOT, load_json, save_json

KST = timezone(timedelta(hours=9))


def main():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    now = datetime.now(KST)
    chs = {k: v for k, v in load_json(ROOT / "upload" / "channels.json").items()
           if not k.startswith("_") and v.get("enabled")}
    rows = []
    for ch, cfg in chs.items():
        logp = ROOT / "upload" / "channels" / ch / "logs" / "uploaded.jsonl"
        if not logp.exists():
            continue
        es = []
        for line in logp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if not e.get("video_id"):
                continue
            pub = datetime.fromisoformat(e.get("publish_at") or (e["at"] + "+09:00"))
            if timedelta(hours=18) <= now - pub <= timedelta(days=7):
                es.append((e, pub))
        if not es:
            continue
        try:
            c = Credentials.from_authorized_user_file(str(ROOT / "upload" / "channels" / ch / "token.json"))
            c.refresh(Request())
            items = build("youtube", "v3", credentials=c).videos().list(
                part="statistics", id=",".join(e["video_id"] for e, _ in es[:50])).execute().get("items", [])
            st = {v["id"]: int(v["statistics"].get("viewCount", 0)) for v in items}
        except Exception as ex:
            print(f"  {ch} 조회 실패: {ex}")
            continue
        vals = [st[e["video_id"]] for e, _ in es if e["video_id"] in st]
        med = max(statistics.median(vals), 1) if vals else 1
        for e, pub in es:
            if e["video_id"] not in st:
                continue
            eid = Path(e.get("file", "")).stem[9:]   # YYYYMMDD_<edl id>
            edl = ROOT / "edl" / ch / f"{eid}.json"
            d = load_json(edl) if edl.exists() else {}
            rows.append({"channel": cfg.get("name", ch), "views": st[e["video_id"]],
                         "ratio": round(st[e["video_id"]] / med, 2),
                         "format": "docad" if "docad" in eid else ("info" if "info" in eid else "ad"),
                         "title": e.get("title"), "topic": d.get("topic_label"), "headline": d.get("headline"),
                         "hook": (d.get("lines") or [{}])[0].get("text"), "published": pub.strftime("%m-%d %H:%M")})
    rows.sort(key=lambda r: -r["ratio"])
    by_fmt = {}
    for r in rows:
        by_fmt.setdefault(r["format"], []).append(r["ratio"])
    save_json(ROOT / "library" / "our_performance.json", {
        "updated": now.strftime("%Y-%m-%d %H:%M"), "n": len(rows),
        "top5": rows[:5], "bottom5": rows[-5:] if len(rows) > 5 else [],
        "format_avg_ratio": {k: round(sum(v) / len(v), 2) for k, v in by_fmt.items()},
        "note": "채널 중앙값 대비 배수. 표본이 적어 우연이 크다 — 한두 개로 방향을 크게 바꾸지 않는다."})
    print(f"우리 영상 {len(rows)}개 비교 → library/our_performance.json")
    for r in rows[:5]:
        print(f"  상위 x{r['ratio']} {r['views']:>6} | {r['channel']} | {r['title']}")


if __name__ == "__main__":
    main()
