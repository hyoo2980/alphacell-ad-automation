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
ANGLES = [  # 12개 — 같은 날 5채널은 서로 다른 각도, 날마다 이동 (12일 주기)
    "식후 졸림·오후 무기력",
    "손발 저림·찌릿함",
    "흰 쌀밥·탄수화물 식습관",
    "밥 위에 뿌려 먹는 사용법(보라색 가루)",
    "자색고구마 안토시아닌 이야기",
    "독일산 귀리 식이섬유 이야기",
    "혈당측정기 수치를 보는 순간(실망 → 안도)",
    "부모님·가족을 챙기는 마음",
    "식약처·연구·제조 과정의 신뢰",
    "택배 개봉·구매 후기형 장면",
    "끈적한 혈액·당독소 비유(애니메이션)",
    "다리 붓기·계단 숨참 같은 일상 신호",
]
# 음성: config/voices.json (provider 별 목록). ElevenLabs 키가 등록되면 elevenlabs 목록을 쓴다.
def _voices():
    import os
    vc = load_json(ROOT / "config" / "voices.json")
    prov = os.environ.get("TTS_PROVIDER") or vc.get("default", "gemini")
    return vc[prov]
LENGTHS = ["25~30초", "35~40초", "30~35초", "40~45초", "28~33초"]


def assign(channel, day):
    chs = load_json(ROOT / "upload" / "channels.json")
    active = [k for k, v in chs.items() if not k.startswith("_") and v.get("enabled")]
    slot = active.index(channel) if channel in active else 0
    # 같은 날 이미 만든 EDL 이 있으면(추가 제작) 모든 채널 배정을 같은 폭만큼 밀어 다른 소재·훅이 나오게.
    # 채널 간 겹침 방지를 유지하려고 shift 는 채널 공통값(오늘 가장 많이 만든 채널의 개수)을 쓴다.
    ymd = day.strftime("%y%m%d")
    shift = max([len(list((ROOT / "edl" / c).glob(f"{ymd}_*.json"))) for c in active] or [0])
    k = (day.toordinal() + slot + shift) % 5
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
    # 참고 스크립트: 참고 스크립트/<group>/*.txt 의 [번호] 블록들을 날짜+채널로 순환 배정 (메시지 영감용)
    import re
    blocks = []
    for f in sorted((ROOT / "참고 스크립트" / cfg.get("group", "")).glob("*.txt")):
        for m in re.finditer(r"^\[(\d+)\]", f.read_text(encoding="utf-8"), re.M):
            blocks.append(f"{f.relative_to(ROOT).as_posix()} [{m.group(1)}]")
    ref = blocks[(day.toordinal() * 5 + slot + shift) % len(blocks)] if blocks else None
    return {
        "channel": channel, "name": cfg.get("name"), "date": day.isoformat(), "slot": slot,
        "style_profile": cfg.get("style_profile"), "brand": cfg.get("brand"),
        "hook_type": HOOKS[k], "angle": ANGLES[(day.toordinal() + shift * 5 + slot * 2) % len(ANGLES)],
        "voice": _voices()[k % len(_voices())], "length": LENGTHS[k],
        "hook_sources": [{"src": x.get("path") or x.get("source"), "visual": x["labels"].get("visual"),
                          "desc": x["labels"].get("desc")} for x in mine],
        "reference_script": ref,
        "avoid_angles": [ANGLES[(day.toordinal() + shift * 5 + i * 2) % len(ANGLES)] for i in range(len(active)) if i != slot],
        "rule": "소재 각도(angle)는 반드시 지킨다(다른 채널과 겹치지 않게 배정된 것). avoid_angles(오늘 다른 채널 소재)는 "
                "헤드라인·훅·제목의 메인 소재로 쓰지 않는다(본문에서 한 번 스치는 정도만). 첫 문구의 컷은 hook_sources 중에서 "
                "고른다. reference_script 는 메시지·논리 영감용일 뿐 문장을 그대로 쓰지 않는다.",
    }


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(json.dumps(assign(sys.argv[1], d), ensure_ascii=False, indent=2))
