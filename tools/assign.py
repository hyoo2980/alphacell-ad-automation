"""오늘의 채널별 제작 배정 — 광고 흐름(PATTERNS HSO)은 고정, 표면(훅·증상·보이스·첫 컷)만 날짜+채널로 돌린다.

  python tools/assign.py <채널id> [YYYY-MM-DD]

같은 날 5채널은 훅 유형/증상 조합과 보이스·첫 컷이 서로 다르게 나온다(같은 영상으로 감지되지 않을 정도).
"""
import json
import sys
from collections import Counter
from datetime import date, timedelta

from common import LIB, ROOT, load_json

HOOKS = [  # PATTERNS_혈당.md 의 검증된 훅 유형
    "증상 질문형 (예: 밥만 먹으면 졸리시죠?)",
    "사과·반전형 (예: 혈당 수치만 잠깐 내리는 걸 찾으셨다면 죄송합니다)",
    "확신형 (예: 장담하는데, 식후 혈당 이렇게 관리해 보세요 — 판매중단·환불 약속 없이)",
    "증상 장면형 (예: 점심 먹고 오후 2시, 눈꺼풀이 내려앉을 때)",
    "사실 제시형 (예: 식후 졸음, 나이 탓이 아닐 수 있어요)",
]
SYMPTOMS = ["식후 졸림·무기력", "손발 저림·찌릿함", "식후 혈당 급상승(흰 쌀밥)"]
SYMPTOM_VISUALS = {  # 훅 증상에 맞는 첫 컷 비주얼
    "식후 졸림·무기력": ("ugc_drowsy_after_meal",),
    "손발 저림·찌릿함": ("ugc_numb_hands", "hands_still", "ugc_leg_pain"),
    "식후 혈당 급상승(흰 쌀밥)": ("glucometer_high", "glucose_test", "meal_sprinkle_rice"),
}
LENGTHS = ["30~35초", "35~40초", "40~45초", "32~38초", "36~42초"]


def _voices():
    import os
    vc = load_json(ROOT / "config" / "voices.json")
    return vc[os.environ.get("TTS_PROVIDER") or vc.get("default", "gemini")]


def source_usage(day, days=14):
    """최근 days 일 동안 모든 채널 EDL 에서 소스별 사용 횟수 (예시 EDL 제외)."""
    cnt = Counter()
    since = (day - timedelta(days=days)).strftime("%y%m%d")
    for p in (ROOT / "edl").glob("*/*.json"):
        if p.parent.name == "examples" or p.name[:6] < since:
            continue
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ln in e.get("lines", []):
            for m in ln.get("media") or []:
                cnt[m.get("src")] += 1
    return cnt


def assign(channel, day):
    chs = load_json(ROOT / "upload" / "channels.json")
    active = [k for k, v in chs.items() if not k.startswith("_") and v.get("enabled")]
    slot = active.index(channel) if channel in active else 0
    ymd = day.strftime("%y%m%d")   # 같은 날 추가 제작이면 전 채널 공통으로 한 칸씩 밀기
    shift = max([len(list((ROOT / "edl" / c).glob(f"{ymd}_*.json"))) for c in active] or [0])
    k = (day.toordinal() + slot + shift) % len(HOOKS)
    cfg = chs[channel]
    items = [x for x in load_json(LIB / "index.json")
             if x.get("group") == cfg.get("group") and x.get("labels")
             and not x["labels"].get("has_text") and (x["labels"].get("quality") or 0) >= 2]
    usage = source_usage(day)
    items.sort(key=lambda x: usage.get(x["path"], 0))
    symptom = SYMPTOMS[(day.toordinal() + slot * 2 + shift) % len(SYMPTOMS)]
    pool = [x for x in items if x["labels"].get("visual") in SYMPTOM_VISUALS[symptom]]
    hook_cuts = (pool[slot % len(pool):] + pool[:slot % len(pool)])[:4] if pool else []   # 채널마다 다른 첫 컷
    voices = _voices()
    return {
        "channel": channel, "name": cfg.get("name"), "date": day.isoformat(),
        "style_profile": cfg.get("style_profile"), "brand": cfg.get("brand"),
        "hook_type": HOOKS[k], "hook_symptom": symptom,
        "voice": voices[(k + slot) % len(voices)], "length": LENGTHS[slot % len(LENGTHS)],
        "first_cut_candidates": [f"{x['labels']['desc']} | {x['path']}" for x in hook_cuts],
        "sources_by_usage": [f"{usage.get(x['path'], 0)}회 | {x['labels']['visual']} | {x['labels']['beat']} | "
                             f"{x['labels']['desc']} | {x['path']}" for x in items],
    }


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(json.dumps(assign(sys.argv[1], d), ensure_ascii=False, indent=2))
