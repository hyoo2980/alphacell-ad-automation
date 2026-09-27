"""완성 영상 → 유튜브 자동 업로드 대기열(upload/inbox) 등록 + 메타데이터(json) 생성.

  python tools/queue_upload.py work/<id>/manifest.json --channel <채널id> [--title "직접 지정"]
  채널별 대기열: upload/channels/<채널id>/inbox (보류는 hold)
  render.py 에 --upload 를 주거나 upload/config.json 의 auto_queue=true 면 렌더 후 자동 호출.

가이드 교훈 반영: 제목·훅 골격이 같은 영상이 연달아 올라가면 쇼츠 노출이 0 이 된 사례 →
제목 틀(template)을 순환하고 최근 제목과 겹치면 다른 틀을 씀.
"""
import argparse
import json
import re
import shutil
from datetime import datetime

from common import ROOT, brand, load_json, plain, save_json

UPLOAD = ROOT / "upload"
TEMPLATES = [
    "{hook}",
    "{h1} {h2}",
    "{h2}, {product}",
    "{hook} 이유가 있었습니다",
    "{h1} 꼭 보세요",
]


def recent_titles(UP, n=30):
    titles = []
    for p in list((UP / "inbox").glob("*.json")) + list((UP / "done").glob("*.json")):
        try:
            titles.append(load_json(p)["title"])
        except Exception:
            pass
    logp = UP / "logs" / "uploaded.jsonl"
    if logp.exists():
        titles += [json.loads(line).get("title", "") for line in logp.read_text(encoding="utf-8").splitlines() if line.strip()]
    return titles[-n:]


def make_title(mf, br, UP):
    hl = [plain(h["text"]).strip() for h in mf.get("headline", [])]
    ctx = {"hook": plain(mf["lines"][0]["text"]).strip(), "h1": hl[0] if hl else "", "h2": hl[1] if len(hl) > 1 else "",
           "product": br.get("product", "").split(" (")[0]}
    state_p = UP / "logs" / "title_state.json"
    state = load_json(state_p) if state_p.exists() else {"next": 0}
    used = set(recent_titles(UP))
    for k in range(len(TEMPLATES)):
        t = TEMPLATES[(state["next"] + k) % len(TEMPLATES)]
        title = re.sub(r"\s+", " ", t.format(**ctx)).strip(" ,")
        if title and title not in used:
            state["next"] = (state["next"] + k + 1) % len(TEMPLATES)
            save_json(state_p, state)
            return title[:100]
    return f"{ctx['hook']} ({datetime.now():%m%d})"[:100]


def queue(manifest_path, channel, title=None, folder="inbox"):
    mf = load_json(manifest_path)
    UP = UPLOAD / "channels" / channel
    cfg = load_json(UPLOAD / "config.json")
    br = brand(load_json(mf["edl"]).get("brand")) if mf.get("edl") else {}
    (UP / folder).mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d}_{mf['id']}"
    dst = UP / folder / f"{name}.mp4"
    shutil.copy2(mf["output"], dst)
    desc = cfg["default_description"]
    if mf.get("disclaimer") and mf["disclaimer"] not in desc:
        desc += "\n" + mf["disclaimer"]
    tags = list(dict.fromkeys(br.get("upload_tags", []) + cfg["default_tags"]))
    edl_title = load_json(mf["edl"]).get("title") if mf.get("edl") else None
    meta = {"title": title or edl_title or make_title(mf, br, UP), "description": desc, "tags": tags,
            "is_short": mf["duration"] <= 180 and cfg["is_short"]}
    save_json(dst.with_suffix(".json"), meta)
    print(f"[업로드 {'대기열' if folder == 'inbox' else folder}] {dst.name}  제목: {meta['title']}")
    return dst


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--title")
    a = ap.parse_args()
    queue(a.manifest, a.channel, a.title)
