# 다음 빈 공개 슬롯 계산 (가이드 6-2). uploaded.jsonl 에서 이미 쓴 슬롯을 읽어
# "아직 안 지났고 비어 있는" 가장 이른 슬롯을 돌려준다. 업로드 실패 기록(video_id 없음)은 슬롯을 차지하지 않는다.
import json
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def _used(log_path, hhmm):
    used = set()
    if not os.path.exists(log_path):
        return used
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            pa = e.get("publish_at")
            if pa and e.get("video_id") and pa[11:16] == hhmm:
                used.add(pa[:10])
    return used


def compute(log_path, slots, now=None):
    now = now or datetime.now(KST)
    used_by_slot = {hhmm: _used(log_path, hhmm) for hhmm in slots}
    day = now.date()
    for _ in range(30):
        for hhmm in slots:
            h, m = map(int, hhmm.split(":"))
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
            if slot <= now or day.isoformat() in used_by_slot[hhmm]:
                continue
            return slot.strftime("%Y-%m-%dT%H:%M:%S+09:00")
        day += timedelta(days=1)
    raise RuntimeError("30일 안에 빈 슬롯을 찾지 못함")
