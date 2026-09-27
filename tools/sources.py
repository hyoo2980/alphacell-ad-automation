"""원본 소스 폴더 → 카탈로그(라벨링용 시트 + 소스별 json).

  .venv/bin/python tools/sources.py scan "<소스 폴더>" --group 혈당
      → library/sources/<group>/catalog.json (파일별 길이·해상도·오디오 여부, labels=null)
      → library/sources/<group>/sheets/sheet_NN.jpg (소스 12개씩, 각 4프레임, 번호 표기)
  라벨은 에이전트가 시트를 보고 catalog.json 의 labels 에 채운다:
      {"visual": "...", "beat": "...", "desc": "...", "has_text": bool, "people": "...", "quality": 1~3}
  .venv/bin/python tools/sources.py index --group 혈당   → library/index.json 갱신(analyze.py index 호출)
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from common import LIB, ROOT, ffmpeg, load_json, probe, save_json

VIDEO = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
PER_SHEET = 12


def _label_font():
    """시트 번호 표기용 한글 폰트(drawtext fontfile). Windows 는 fontconfig 기본 설정이 없어 직접 지정해야 한다."""
    from common import font_path
    try:
        f = font_path("Gmarket Sans TTF", True)
    except FileNotFoundError:
        f = next((c for c in ["C:/Windows/Fonts/malgun.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                              "/System/Library/Fonts/AppleSDGothicNeo.ttc"] if Path(c).exists()), None)
    return (":fontfile='" + f.replace("\\", "/").replace(":", "\\:") + "'") if f else ""


def scan(folder, group):
    folder = Path(folder).resolve()
    out = LIB / "sources" / group
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    cat_path = out / "catalog.json"
    old = {x["path"]: x for x in load_json(cat_path)} if cat_path.exists() else {}
    global FONT
    FONT = _label_font()
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO | IMAGE)
    items, seen = [], {}
    for i, p in enumerate(files):
        rel = p.relative_to(ROOT).as_posix()   # 서버(Linux)에서도 같은 경로
        h = hashlib.md5(p.read_bytes()).hexdigest()
        if h in seen:
            print(f"[중복] {p.name} = {seen[h]}")
            continue
        seen[h] = p.name
        info = probe(p) if p.suffix.lower() in VIDEO else {**probe(p), "duration": 0.0}
        thumb = out / "thumbs" / f"{len(items):03d}.jpg"
        label = f"{len(items)}  {p.name[:34]}  {info['w']}x{info['h']}  {info['duration']:.1f}s".replace(":", "\\:").replace("'", "")
        if p.suffix.lower() in VIDEO:
            d = info["duration"]
            ts = [d * k for k in (0.08, 0.35, 0.62, 0.9)]
            sel = "+".join(f"between(t\\,{t:.2f}\\,{t + 0.05:.2f})" for t in ts)
            ffmpeg("-i", p, "-vf", f"select='{sel}',scale=240:240:force_original_aspect_ratio=decrease,"
                   "pad=240:240:(ow-iw)/2:(oh-ih)/2,tile=4x1,pad=iw:ih+28:0:28,"
                   f"drawtext=text='{label}'{FONT}:x=6:y=5:fontsize=18:fontcolor=yellow",
                   "-frames:v", "1", "-fps_mode", "vfr", thumb)
        else:
            ffmpeg("-i", p, "-vf", "scale=240:240:force_original_aspect_ratio=decrease,pad=960:240:0:0,"
                   f"pad=iw:ih+28:0:28,drawtext=text='{label}'{FONT}:x=6:y=5:fontsize=18:fontcolor=yellow", thumb)
        prev = old.get(rel, {})
        items.append({"idx": len(items), "path": rel, "name": p.name, "md5": h, "kind": "source", "group": group,
                      "type": "video" if p.suffix.lower() in VIDEO else "image",
                      "w": info["w"], "h": info["h"], "duration": round(info["duration"], 2),
                      "has_audio": info.get("has_audio", False), "thumb": thumb.relative_to(ROOT).as_posix(),
                      "labels": prev.get("labels")})
    for s in range(0, len(items), PER_SHEET):
        chunk = [ROOT / x["thumb"] for x in items[s:s + PER_SHEET]]
        args = []
        for c in chunk:
            args += ["-i", c]
        ffmpeg(*args, "-filter_complex", f"vstack=inputs={len(chunk)}" if len(chunk) > 1 else "null",
               out / "sheets" / f"sheet_{s // PER_SHEET:02d}.jpg")
    save_json(cat_path, items)
    print(f"{len(items)}개 소스 → {cat_path}  (시트 {(len(items) + PER_SHEET - 1) // PER_SHEET}장)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "index"])
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--group", required=True)
    a = ap.parse_args()
    if a.cmd == "scan":
        scan(a.folder, a.group)
    else:
        subprocess.run([sys.executable, str(ROOT / "tools/analyze.py"), "index"], check=True)
