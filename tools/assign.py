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
TITLE_STYLES = [  # 제목 스타일 순환 (같은 날 채널끼리 다르게, 인기 제목 패턴 중 하나만)
    "숫자형 — '~하는 3가지', '40초만 따라하세요'",
    "반전·진실형 — '~의 진실', '알고 보니 ~였어요'",
    "질문형 — '~하시나요?', '왜 ~할까?'",
    "경고·자극형 — '먹자마자 혈당 폭발!', '이거 모르면 ~'",
    "경험·고백형 — '~했더니 ~ 싹 잡혔어요', '저도 몰랐어요'",
    "상황 묘사형 — '점심 먹고 2시, 눈꺼풀이 내려앉는다면'",
    "비교형 — 'A 말고 B', '~보다 먼저 챙길 것'",
]
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


def base_scripts(group):
    """참고 스크립트/<group>/*.txt 의 '[스크립트 N]' 블록들 → [(파일명 N, 본문)]."""
    import re
    out = []
    for f in sorted((ROOT / "참고 스크립트" / group).glob("*.txt")):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^\[스크립트\s*(\d+)\]\s*\n(.*?)(?=^\[스크립트|\Z)", t, re.M | re.S):
            out.append((f"{f.stem} #{m.group(1)}", m.group(2).strip()))
    return out


SHARED_VISUALS = ("product_", "coupang_search", "sign_discount", "meal_sprinkle_rice", "powder_purple", "ugc_intake")


def channel_pool(items, slot, n):
    """채널별 소스 분리: 필수 장면(제품·뿌리기·쿠팡 등)은 공용, 나머지는 채널마다 1/n 씩 나눠 주로 쓰게 한다.
    같은 날 8채널이 같은 컷을 쓰지 않게(유사 영상 노출 제한 방지). 자기 묶음이 모자라면 다른 소스도 뒤에 붙인다."""
    shared = [x for x in items if x["labels"]["visual"].startswith(SHARED_VISUALS)]
    rest = [x for x in items if x not in shared]
    rest_sorted = sorted(rest, key=lambda x: x["path"])
    mine = [x for i, x in enumerate(rest_sorted) if i % n == slot]
    others = [x for x in rest if x not in mine]
    return mine + shared, others


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
    bs = base_scripts(cfg.get("group", ""))
    base = bs[(day.toordinal() * 5 + slot + shift) % len(bs)] if bs else None   # 같은 날 채널끼리 다른 번호, 날마다 이동
    return {
        "base_script_id": base[0] if base else None,
        "base_script": base[1] if base else None,
        "channel": channel, "name": cfg.get("name"), "date": day.isoformat(),
        "style_profile": cfg.get("style_profile"), "brand": cfg.get("brand"),
        "hook_type": HOOKS[k], "hook_symptom": symptom,
        "voice": {**voices[(day.toordinal() * 5 + slot + shift) % len(voices)], "account": slot},   # 목소리 14종 순환   # account: 채널 담당 ElevenLabs 계정
        "length": LENGTHS[slot % len(LENGTHS)],
        "title_style": TITLE_STYLES[(day.toordinal() * 3 + slot + shift) % len(TITLE_STYLES)],
        "first_cut_candidates": [f"{x['labels']['desc']} | {x['path']}" for x in hook_cuts],
        "sources_by_usage": [f"{usage.get(x['path'], 0)}회 | {x['labels']['visual']} | {x['labels']['beat']} | "
                             f"{x['labels']['desc']} | {x['path']}" for x in channel_pool(items, slot, len(active))[0]],
        "sources_other_channels": [f"{x['labels']['visual']} | {x['labels']['desc']} | {x['path']}"
                                   for x in channel_pool(items, slot, len(active))[1]],
        "source_rule": "sources_by_usage(이 채널 전용 묶음 + 필수 공용)에서 먼저 고른다. 맞는 게 정말 없을 때만 sources_other_channels.",
    }


INFO_TOPICS = ["식후졸음", "혈당 스파이크", "손발 저림·붓기", "끈적한 혈액", "인슐린 저항성", "공복혈당", "당화혈색소",
               "계절·명절", "뱃살", "50대 이후", "쌀밥·국수", "수면", "스트레스", "갈증", "계단 숨참"]
INFO_PATTERNS = ["질문형", "의외의 사실", "일상 사례(예를 들어 ~하는 50대)", "체크리스트·Q&A"]
INFO_VOICES = ["ko-KR-Neural2-C", "ko-KR-Neural2-A", "ko-KR-Neural2-B", "ko-KR-Wavenet-C", "ko-KR-Wavenet-D"]
INFO_BAN_NAMES = ("58_", "62_", "12_58_", "20260817_164349", "쿠팡", "약사섭취", "0815", "0816", "IMG_3463", "IMG_7333",
                  "제품컷2")
INFO_BAN_VISUALS = ("sign_discount", "secret_link_page", "coupang_search", "show_host", "person_studio", "ugc_show_box",
                    "ugc_show_stick", "ugc_intake", "emergency_119", "import_customs", "warehouse_shipping",
                    "prior_ad_doctor", "authority_doctor")


def assign_info(channel, day, ad=False):
    """정보형 쇼츠 배정: 주제(15)·패턴(4)·보이스(5) 순환 + 사용 가능한 소스(금지 소스·세로 영상 제외, 길이 포함)."""
    chs = load_json(ROOT / "upload" / "channels.json")
    active = [k for k, v in chs.items() if not k.startswith("_") and v.get("enabled")]
    slot = active.index(channel) if channel in active else 0
    ymd = day.strftime("%y%m%d")
    shift = max([len(list((ROOT / "edl" / c).glob(f"{ymd}_*{'docad' if ad else 'info'}*.json"))) for c in active] or [0])
    o = day.toordinal()
    usage = source_usage(day)
    items = [x for x in load_json(LIB / "index.json")
             if x.get("labels") and not x["labels"].get("has_text") and (x["labels"].get("quality") or 0) >= 2
             and not any(b in x["name"] for b in INFO_BAN_NAMES) and x["labels"].get("visual") not in INFO_BAN_VISUALS
             and not ((x.get("h") or 0) > (x.get("w") or 0))]
    items.sort(key=lambda x: usage.get(x["path"], 0))
    refs = {}
    for folder in ("경쟁사_바르통SOD", "경쟁사_솔티스혈관클리어"):
        fs = sorted((ROOT / "참고 스크립트" / folder).glob("*.txt"))
        refs[folder] = [f.relative_to(ROOT).as_posix() for f in (fs[(o * 8 + slot + i) % len(fs)] for i in range(2))] if fs else []
    return {
        "channel": channel, "date": day.isoformat(), "format": "info",
        "topic": INFO_TOPICS[(o * 8 + slot + shift + (7 if ad else 0)) % len(INFO_TOPICS)],
        "pattern": INFO_PATTERNS[(o + slot + shift + (2 if ad else 0)) % len(INFO_PATTERNS)],
        "voice": ({**_voices()[(o * 5 + slot) % len(_voices())], "account": slot} if ad else
                  {"provider": "google", "voice": INFO_VOICES[(o + slot) % len(INFO_VOICES)], "rate": 1.08}),
        "base_script": (lambda b: b[(o * 5 + slot + shift) % len(b)][1] if b else None)(base_scripts("혈당")) if ad else None,
        "title_style": TITLE_STYLES[(o * 3 + slot + shift) % len(TITLE_STYLES)],
        "product_script": "참고 스크립트/혈당/참고스크립트1.txt",
        "competitor_hooks": refs,
        "sources_by_usage": [f"{usage.get(x['path'], 0)}회 | {x.get('duration', 0)}초 | {x['labels']['visual']} | "
                             f"{x['labels']['desc']} | {x['path']}" for x in channel_pool(items, slot, len(active))[0]],
        "sources_other_channels": [f"{x.get('duration', 0)}초 | {x['labels']['visual']} | {x['labels']['desc']} | {x['path']}"
                                   for x in channel_pool(items, slot, len(active))[1]],
        "source_rule": "sources_by_usage(이 채널 전용 묶음 + 필수 공용)에서 먼저 고른다. 맞는 게 정말 없을 때만 sources_other_channels.",
    }


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else date.today()
    if "--info" in sys.argv:
        print(json.dumps(assign_info(sys.argv[1], d, ad="--ad" in sys.argv), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(assign(sys.argv[1], d), ensure_ascii=False, indent=2))
