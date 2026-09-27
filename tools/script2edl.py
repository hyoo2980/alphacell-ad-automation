"""광고 스크립트(대본) → EDL 초안. 에이전트가 결과를 검토·수정한 뒤 check → render.

  .venv/bin/python tools/script2edl.py briefs/scripts/xxx.txt --brand alphacell --profile hyeoldang \
      --group 혈당 --id 260927_alphacell_s01_script --headline "노랑줄|{red|빨강} {green|초록}|알파셀 혈당 세이프"

동작
  1) 오타 교정(brand.script_rules.typo_fix) → 문장/쉼표 단위 → 공백 제외 13자 이내 자막 문구로 분할 (≈1초)
  2) 핵심어 강조: script_rules.highlight 정규식 → {색|단어} (문구당 최대 2개)
  3) 컷 매칭: script_rules.visual 정규식 → catalog 의 labels.visual 후보 중 덜 쓴 컷 선택
     같은 비주얼이 이어지면 컷을 이어 씀(최대 2문구), 매칭 없으면 위치(문제→원인→메커니즘→결과)로 선택
  4) 스크립트에 구매 유도(CTA)가 없으면 brand.script_rules.cta_lines 를 끝에 붙임 (--no-cta 로 끔)
  5) 헤드라인은 --headline 으로 지정(3줄은 | 로 구분). 없으면 첫 문장에서 자동 생성(에이전트가 다듬을 것)
"""
import argparse
import re
from collections import Counter
from pathlib import Path

from common import LIB, ROOT, brand, load_json, save_json

LIMIT = 13            # 자막 한 문구 최대 글자수(공백 제외)
MAX_CHAIN = 2         # 한 컷에 이어 붙일 최대 문구 수


def nospace(s):
    return len(re.sub(r"\s", "", s))


def split_phrases(script):
    """문장 → 쉼표/연결어미 → 어절 누적으로 13자 이내 문구."""
    script = re.sub(r"\s+", " ", script.replace('"', "").replace("“", "").replace("”", "")).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", script) if s.strip()]
    out = []
    for si, sen in enumerate(sentences):
        clauses = [c.strip() for c in re.split(r"(?<=,)\s+", sen) if c.strip()]
        for c in clauses:
            words, cur = c.split(" "), []
            for w in words:
                # 의존명사·보조용언(수/것/거/때/줄/듯/있/없…)으로 시작하는 어절은 앞 어절과 떨어뜨리지 않음
                bound = re.match(r"^(수|것|거|게|때|줄|듯|뿐|있|없|하는|되는)", w)
                if cur and nospace(" ".join(cur + [w])) > (LIMIT + 5 if bound else LIMIT):
                    out.append((si, " ".join(cur)))
                    cur = []
                cur.append(w)
            if cur:
                out.append((si, " ".join(cur)))
    out = [(si, re.sub(r"[.,]+$", "", p).strip()) for si, p in out if re.sub(r"[.,]", "", p).strip()]
    # 3자 이하 조각 병합: 접속어(근데/그래서…)는 다음 문구 앞에, 어미 조각(거예요/바뀌고…)은 앞 문구 뒤에
    merged = []
    k = 0
    while k < len(out):
        si, p = out[k]
        if nospace(p) <= 3 and re.match(r"^(근데|그런데|그래서|그러니|바로|이제|결국|사실)$", p) and k + 1 < len(out):
            out[k + 1] = (out[k + 1][0], f"{p} {out[k + 1][1]}")
        elif nospace(p) <= 3 and merged and merged[-1][0] == si and nospace(merged[-1][1] + p) <= LIMIT + 5:
            merged[-1] = (si, f"{merged[-1][1]} {p}")
        else:
            merged.append((si, p))
        k += 1
    # 병합으로 길어진 문구는 다시 반으로 (어절 기준)
    final = []
    for si, p in merged:
        if nospace(p) > LIMIT + 3:
            w = p.split(" ")
            cut = min(range(1, len(w)), key=lambda j: abs(nospace(" ".join(w[:j])) - nospace(p) / 2))
            final += [(si, " ".join(w[:cut])), (si, " ".join(w[cut:]))]
        else:
            final.append((si, p))
    return final


def highlight(text, rules, max_n=2):
    spans = []
    for col, pat in rules:
        for m in re.finditer(rf"(?<![가-힣0-9A-Za-z])(?:{pat})", text):   # 단어 중간 매칭 금지
            if any(not (m.end() <= a or m.start() >= b) for a, b, _ in spans):
                continue
            spans.append((m.start(), m.end(), col))
    spans = sorted(spans, key=lambda x: -(x[1] - x[0]))[:max_n]
    for a, b, col in sorted(spans, reverse=True):
        text = f"{text[:a]}{{{col}|{text[a:b]}}}{text[b:]}"
    return text


def load_catalog(group):
    items = []
    for p in (LIB / "sources" / group / "catalog.json", LIB / "stock" / group / "catalog.json"):
        if p.exists():
            items += [x for x in load_json(p) if x.get("labels")]
    for p in (LIB / "generated").glob("*.json"):
        items.append(load_json(p))
    return [x for x in items if not x["labels"].get("has_text") and (x["labels"].get("quality") or 2) >= 2]


def build(script, brand_name, profile, group, vid, headline=None, cta=True, layout="band"):
    br = brand(brand_name)
    R = br["script_rules"]
    for a, b in R.get("typo_fix", {}).items():
        script = script.replace(a, b)
    phrases = split_phrases(script)
    cat = load_catalog(group)
    by_visual = {}
    for x in cat:
        by_visual.setdefault(x["labels"]["visual"], []).append(x)
    by_beat = {}
    for x in cat:
        by_beat.setdefault(x["labels"].get("beat"), []).append(x)
    used = Counter()
    recent = []

    def pick(cands):
        cands = [c for c in cands if c["path"] not in recent[-4:]] or cands
        if not cands:
            return None
        c = min(cands, key=lambda c: (used[c["path"]], -(c["labels"].get("quality") or 2)))
        used[c["path"]] += 1
        recent.append(c["path"])
        return c

    def media_for(c):
        m = {"src": c["path"]}
        if c.get("type") == "video" and c.get("duration", 0) > 4:
            m["in"] = round(min(2.0, c["duration"] * 0.15) * (used[c["path"]] - 1 + 1) % max(1, c["duration"] - 2), 2)
        return [m]

    lines, prev_key, chain, n = [], None, 0, len(phrases)
    for i, (si, text) in enumerate(phrases):
        key = None
        for pat, visuals in R["visual"]:
            if re.search(pat, text):
                key = tuple(visuals)
                break
        ln = {"text": highlight(text, R["highlight"]), "_sentence": si}
        if lines and chain < MAX_CHAIN and ((key and key == prev_key) or not key):
            chain += 1                                  # 같은 장면이 이어지거나 매칭이 없으면 컷 유지
            if not key:
                key = prev_key
        else:
            cands = [c for v in (key or ()) for c in by_visual.get(v, [])]
            if not cands:                               # 매칭 실패 → 위치 기반 비트
                pos = i / max(1, n - 1)
                beat = [b for b, start in R["visual_by_position"] if pos >= start][-1]
                cands = by_beat.get(beat, [])
            c = pick(cands)
            if c:
                ln["media"] = media_for(c)
                ln["_visual"] = c["labels"]["visual"]
            chain = 1
        prev_key = key
        lines.append(ln)
    if lines:
        lines[0]["style"] = "outline"                   # 훅 첫 문구는 외곽선형 (참고영상 공통)

    if cta and not re.search(R["cta_trigger"], script):
        for c in R["cta_lines"]:
            ln = {k: v for k, v in c.items() if k != "visual"}
            pc = pick([x for v in c["visual"] for x in by_visual.get(v, [])])
            if pc:
                ln["media"] = media_for(pc)
            ln["_cta"] = True
            lines.append(ln)

    if headline:
        parts = headline.split("|") if "{" not in headline else re.split(r"\|(?![^{]*\})", headline)
        cols = ["yellow", "white", "purple"]
        hl = [{"text": t.strip(), "color": cols[k] if k < 3 else "white"} for k, t in enumerate(parts)]
    else:
        first = re.sub(r"\{(\w+)\|([^}]*)\}", r"\2", lines[0]["text"])
        hl = [{"text": first, "color": "yellow"},
              {"text": "{red|식후 혈당} {green|한 포로 관리}", "color": "white"},
              {"text": br["product"].split(" (")[0], "color": "purple"}]

    edl = {"id": vid, "_source": "script", "brand": brand_name, "style_profile": profile, "layout": layout,
           "headline": hl, "disclaimer": True, "voice": {"provider": "gemini"}, "bgm": None, "lines": lines}
    return edl


def table(edl):
    for i, ln in enumerate(edl["lines"]):
        m = (ln.get("media") or [{}])[0].get("src", "  (이어짐)")
        print(f"{i:2d} {ln.get('style', 'box'):7s} {ln['text'][:38]:40s} {ln.get('_visual', ''):22s} {Path(m).name[:40]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="대본 txt 경로")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--headline")
    ap.add_argument("--layout", default="band")
    ap.add_argument("--no-cta", action="store_true")
    a = ap.parse_args()
    edl = build(Path(a.script).read_text(encoding="utf-8"), a.brand, a.profile, a.group, a.id,
                a.headline, not a.no_cta, a.layout)
    out = ROOT / "edl" / f"{a.id}.json"
    save_json(out, edl)
    table(edl)
    print(f"→ {out}  (문구 {len(edl['lines'])}개)")
