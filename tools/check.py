"""렌더 전 EDL 검사: 위험 표현, 자막/헤드라인 길이, 소스 존재, HSO 구성.

  .venv/bin/python tools/check.py edl/v001.json
경고(WARN)는 사람이 판단, 오류(ERR)는 렌더 전에 반드시 수정.
"""
import sys

from common import ROOT, brand, load_json, plain, set_profile, style


def check(path):
    edl = load_json(path)
    set_profile(edl.get("style_profile"))
    st, br = style(), brand(edl.get("brand"))
    risky = br["claims_risky"]["phrases"]
    maxc = st["subtitle_rules"]["max_chars_per_line"]
    issues = []

    for i, h in enumerate(edl.get("headline", [])):
        if len(plain(h["text"])) > 13:
            issues.append(("WARN", f"헤드라인 {i + 1}줄 {len(plain(h['text']))}자 > 13자 → 줄여 쓰세요(자동 축소로 글씨가 작아짐)"))
    texts = [("headline", plain(h["text"])) for h in edl.get("headline", [])]
    for i, ln in enumerate(edl["lines"]):
        texts += [(f"line {i}", plain(ln["text"])), (f"line {i} vo", ln.get("vo", ""))]
        if len(plain(ln["text"]).replace(" ", "")) > maxc:
            issues.append(("WARN", f"line {i} 자막 {len(plain(ln['text']).replace(' ', ''))}자 — 문구를 나누세요: {ln['text']}"))
        if ln.get("style", "box") not in st["subtitle_styles"]:
            issues.append(("ERR", f"line {i} 알 수 없는 style {ln.get('style')}"))
        for m in ln.get("media") or []:
            if not (ROOT / m["src"]).exists():
                issues.append(("ERR", f"line {i} 소스 없음: {m['src']}"))
            if "library/reference/" in m["src"]:
                issues.append(("INFO", f"line {i} 참고영상 컷 사용(360p·자막블러) → 원본 소스로 교체 권장"))
    if not edl.get("title"):
        issues.append(("WARN", "title 없음 → 유튜브 제목을 EDL 에 직접 쓰세요(40자 이내, 헤드라인 이어붙이기 금지)"))
    if not edl["lines"][0].get("media"):
        issues.append(("ERR", "첫 문구에 media 없음"))
    for p in (br.get("cta_shorts") or {}).get("forbidden", []):
        if any(p in t for _, t in texts):
            issues.append(("WARN", f"쇼츠에 링크 유도 CTA '{p}' → '지금 쿠팡에 알파셀 혈당 세이프를 검색해보세요!' 로"))
            break
    for where, t in texts:
        for p in risky:
            if p and p in t:
                issues.append(("WARN", f"[표현위험] {where}: '{p}' → {t}"))

    n = len(edl["lines"])
    est = sum(len(ln.get("vo", plain(ln["text"])).replace(" ", "")) for ln in edl["lines"]) / 7.0
    if (edl.get("voice") or {}).get("provider") == "say":
        issues.append(("ERR", "납품 영상에 macOS 기본 음성(say) 금지 — gemini 또는 elevenlabs"))
    if not edl.get("disclaimer"):
        issues.append(("WARN", "고지 문구(disclaimer) 없음 — 연출/개인차 고지 권장"))
    issues.append(("INFO", f"문구 {n}개, 예상 길이 ≈ {est:.0f}초"))
    for lvl, msg in issues:
        print(f"[{lvl}] {msg}")
    return not any(l == "ERR" for l, _ in issues)


def risky(path):
    """[표현위험] 경고 목록 (자동 업로드 보류 판단용)."""
    edl = load_json(path)
    set_profile(edl.get("style_profile"))
    words = brand(edl.get("brand"))["claims_risky"]["phrases"]
    texts = [plain(h["text"]) for h in edl.get("headline", [])] + [plain(ln["text"]) for ln in edl["lines"]]
    return sorted({w for t in texts for w in words if w and w in t})


if __name__ == "__main__":
    sys.exit(0 if check(sys.argv[1]) else 1)
