"""업로드한 영상 조회수를 계정별 표로 (사람이 요청할 때 로컬에서 실행).

  python tools/views_report.py            # 전체 기간
  python tools/views_report.py --days 7   # 최근 7일 업로드분만

- 영상 목록: upload/channels/<id>/logs/uploaded.jsonl (삭제한 영상 제외)
- 조회수·좋아요·댓글: YouTube Data API videos.list(statistics) — 50개당 쿼터 1유닛
- 결과: 화면 출력 + reports/views_<날짜>.md
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ROOT, load_json

KST = timezone(timedelta(hours=9))


def creds(ch):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    c = Credentials.from_authorized_user_file(str(ROOT / "upload" / "channels" / ch / "token.json"))
    c.refresh(Request())
    return c


def main():
    from googleapiclient.discovery import build
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else None
    since = datetime.now(KST) - timedelta(days=days) if days else None
    chs = {k: v for k, v in load_json(ROOT / "upload" / "channels.json").items()
           if not k.startswith("_") and v.get("enabled")}
    out = [f"# 업로드 영상 조회수 ({datetime.now(KST):%Y-%m-%d %H:%M} 기준)\n"]
    total_all = 0
    for ch, cfg in chs.items():
        logp = ROOT / "upload" / "channels" / ch / "logs" / "uploaded.jsonl"
        es = [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines() if l.strip()] if logp.exists() else []
        es = [e for e in es if e.get("video_id")]
        if since:
            es = [e for e in es if datetime.fromisoformat(e["at"]).replace(tzinfo=KST) >= since]
        rows, total = [], 0
        if es:
            try:
                yt = build("youtube", "v3", credentials=creds(ch))
                stats = {}
                for i in range(0, len(es), 50):
                    r = yt.videos().list(part="statistics,status",
                                         id=",".join(e["video_id"] for e in es[i:i + 50])).execute()
                    stats.update({x["id"]: x for x in r.get("items", [])})
            except Exception as ex:
                out.append(f"## {cfg.get('name', ch)}\n조회 실패: {ex}\n")
                continue
            for e in sorted(es, key=lambda e: e.get("publish_at") or e["at"]):
                x = stats.get(e["video_id"])
                if not x:
                    continue   # 삭제된 영상
                s = x["statistics"]
                v = int(s.get("viewCount", 0))
                total += v
                when = (e.get("publish_at") or e["at"])[:16].replace("T", " ")
                state = "공개" if x["status"]["privacyStatus"] == "public" else "예약/비공개"
                rows.append(f"| {when} | {e.get('title', '')[:28]} | {v:,} | {int(s.get('likeCount', 0)):,} | "
                            f"{int(s.get('commentCount', 0)):,} | {state} | https://youtu.be/{e['video_id']} |")
        total_all += total
        out.append(f"## {cfg.get('name', ch)} — {len(rows)}편, 조회수 합계 {total:,}\n")
        out.append("| 공개(예정) 시각 | 제목 | 조회수 | 좋아요 | 댓글 | 상태 | 링크 |\n|---|---|---|---|---|---|---|")
        out += rows or ["| – | 영상 없음 | | | | | |"]
        out.append("")
    out.append(f"**전체 조회수 합계: {total_all:,}**")
    text = "\n".join(out)
    print(text)
    rp = ROOT / "reports" / f"views_{datetime.now(KST):%y%m%d_%H%M}.md"
    rp.parent.mkdir(exist_ok=True)
    rp.write_text(text, encoding="utf-8")
    print(f"\n저장: {rp.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
