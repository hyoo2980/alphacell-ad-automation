"""오늘의 채널별 제작 배정 — 5채널이 동시에 돌아도 서로 겹치지 않게 날짜+채널 순번으로 축을 나눈다.

  python tools/assign.py <채널id> [YYYY-MM-DD]

같은 날 채널끼리는 훅 유형·소재 각도·보이스·첫 장면 소스 후보가 전부 다르고, 날마다 한 칸씩 돈다.
(유튜브: 영상 간 차이 없는 기계적 대량생산 → 노출 제한. 채널 간 유사 영상 방지)
"""
import json
import sys
from datetime import date

from common import LIB, ROOT, load_json

HOOKS = [
    "증상 질문형 — 식후 몸의 신호를 묻는다 (예: 밥만 먹으면 눈꺼풀이 무거우시죠?)",
    "사과·반전형 — 예상을 뒤집으며 시작 (예: ~찾고 계셨다면 죄송합니다)",
    "상황 장면형 — 구체적 시각·장소의 한 장면 (예: 점심 먹고 오후 2시, 모니터 앞에서…)",
    "사실 제시형 — 흔한 오해를 뒤집는 한 문장 (예: 흰 쌀밥이 문제가 아니었습니다)",
    "확신·도발형 — 강한 확신으로 멈추게 함 (판매중단·환불·100% 같은 위험 표현 없이)",
]
ANGLES = [
    "식후 졸림·오후 무기력",
    "손발 저림·찌릿함",
    "흰 쌀밥·탄수화물 식습관",
    "밥 위에 뿌려 먹는 사용법(보라색 가루)",
    "성분 이야기(자색고구마·귀리 식이섬유)",
]
VOICES = ["Puck", "Orus", "Fenrir", "Sadachbia", "Charon"]
LENGTHS = ["25~30초", "35~40초", "30~35초", "40~45초", "28~33초"]


def assign(channel, day):
    chs = load_json(ROOT / "upload" / "channels.json")
    active = [k for k, v in chs.items() if not k.startswith("_") and v.get("enabled")]
    slot = active.index(channel) if channel in active else 0
    k = (day.toordinal() + slot) % 5
    cfg = chs[channel]
    idx = load_json(LIB / "index.json")
    items = idx if isinstance(idx, list) else idx.get("items", [])
    pool = []
    for x in items:
        lb = x.get("labels") or {}
        if (x.get("group") == cfg.get("group") and lb.get("beat") in ("hook", "problem", "agitate", "enemy")
                and not lb.get("has_text") and (lb.get("quality") or 0) >= 2):
            pool.append(x)
    mine = [x for i, x in enumerate(pool) if i % 5 == k] or pool
    return {
        "channel": channel, "name": cfg.get("name"), "date": day.isoformat(), "slot": slot,
        "style_profile": cfg.get("style_profile"), "brand": cfg.get("brand"),
        "hook_type": HOOKS[k], "angle": ANGLES[(k + 2) % 5], "voice": VOICES[k], "length": LENGTHS[k],
        "hook_sources": [{"src": x.get("path") or x.get("source"), "visual": x["labels"].get("visual"),
                          "desc": x["labels"].get("desc")} for x in mine],
        "rule": "첫 문구의 컷은 hook_sources 중에서 고른다. 나머지 컷도 최근 14일 EDL 에서 첫 컷으로 쓰인 소스는 피한다.",
    }


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(json.dumps(assign(sys.argv[1], d), ensure_ascii=False, indent=2))
