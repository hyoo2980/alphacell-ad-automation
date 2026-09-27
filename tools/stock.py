"""무료 스톡 영상 검색·다운로드 (Pexels / Pixabay, 상업 이용 가능·출처표기 불필요) → 소스 카탈로그 편입.

키 발급(무료): Pexels  https://www.pexels.com/api/  → .env PEXELS_API_KEY
             Pixabay https://pixabay.com/api/docs/ → .env PIXABAY_API_KEY
  .venv/bin/python tools/stock.py search "tired man after lunch" --group 혈당 --n 4 --visual ugc_drowsy_after_meal --beat hook
  → library/stock/<group>/<provider>_<id>.mp4 + library/stock/<group>/catalog.json (라벨 포함)
실제 인물·장면은 한국인이 아닐 수 있으므로 시트로 확인 후 사용. 인물 영상을 '실제 후기'처럼 쓰지 않는다.
"""
import argparse
import os

import requests

from common import LIB, ROOT, load_env, load_json, probe, save_json


def _pexels(q, n, orientation):
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                     params={"query": q, "per_page": n, "orientation": orientation, "size": "medium"}, timeout=60)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = [f for f in v["video_files"] if f.get("width") and f["width"] <= 1920 and f.get("file_type") == "video/mp4"]
        if files:
            best = max(files, key=lambda f: f["width"])
            out.append({"provider": "pexels", "id": v["id"], "url": best["link"], "page": v["url"],
                        "author": v.get("user", {}).get("name"), "duration": v.get("duration")})
    return out


def _pixabay(q, n, orientation):
    r = requests.get("https://pixabay.com/api/videos/", params={"key": os.environ["PIXABAY_API_KEY"], "q": q,
                                                                 "per_page": max(3, n), "safesearch": "true"}, timeout=60)
    r.raise_for_status()
    out = []
    for v in r.json().get("hits", [])[:n]:
        f = v["videos"].get("large") or v["videos"].get("medium")
        if f and f.get("url"):
            out.append({"provider": "pixabay", "id": v["id"], "url": f["url"], "page": v["pageURL"],
                        "author": v.get("user"), "duration": v.get("duration")})
    return out


def search(query, group, n=4, visual=None, beat=None, orientation="landscape"):
    load_env()
    providers = [p for p, k in (("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY")) if os.environ.get(k)]
    if not providers:
        raise SystemExit("PEXELS_API_KEY 또는 PIXABAY_API_KEY 가 .env 에 필요합니다 (무료 발급)")
    out_dir = LIB / "stock" / group
    out_dir.mkdir(parents=True, exist_ok=True)
    cat_path = out_dir / "catalog.json"
    cat = load_json(cat_path) if cat_path.exists() else []
    have = {(c["provider"], c["stock_id"]) for c in cat}
    got = []
    for p in providers:
        for it in globals()[f"_{p}"](query, n, orientation):
            if (p, it["id"]) in have:
                continue
            dst = out_dir / f"{p}_{it['id']}.mp4"
            with requests.get(it["url"], stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            info = probe(dst)
            entry = {"idx": len(cat), "path": str(dst.relative_to(ROOT)), "kind": "stock", "group": group,
                     "provider": p, "stock_id": it["id"], "page": it["page"], "author": it["author"],
                     "type": "video", "w": info["w"], "h": info["h"], "duration": round(info["duration"], 2),
                     "query": query, "labels": {"visual": visual, "beat": beat, "desc": query,
                                                "has_text": False, "quality": None}}
            cat.append(entry)
            got.append(entry)
            print(f"[{p}] {dst.name} {info['w']}x{info['h']} {info['duration']:.1f}s  ← {it['page']}")
    save_json(cat_path, cat)
    print(f"{len(got)}개 추가 → {cat_path}")
    return got


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["search"])
    ap.add_argument("query")
    ap.add_argument("--group", required=True)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--visual")
    ap.add_argument("--beat")
    ap.add_argument("--orientation", default="landscape")
    a = ap.parse_args()
    search(a.query, a.group, a.n, a.visual, a.beat, a.orientation)
