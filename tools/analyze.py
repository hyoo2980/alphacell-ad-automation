"""참고영상 분석: 중복 제거 → 레이아웃(헤드라인/영상/배너) 검출 → 컷 분할 → 음성 전사 → 컷별 클립/키프레임 추출.

사용:
  .venv/bin/python tools/analyze.py run            # 참고영상 소스 폴더 전체 분석
  .venv/bin/python tools/analyze.py run a.mp4 b.mp4
  .venv/bin/python tools/analyze.py index          # 라벨링된 analysis.json 들을 library/index.json 으로 합침

결과: library/reference/<id>/
  analysis.json   컷 목록(시간, 전사, 클립 경로, labels=null → 에이전트가 채움)
  sheet.jpg       컷 번호가 찍힌 컨택트시트 (라벨링용)
  shots/NNN.mp4   영상 영역만 잘라낸 컷 클립 (헤드라인/배너 제외, 자막은 아직 포함)
  shots/NNN.jpg   컷 중앙 키프레임
"""
import hashlib
import re
import sys
from pathlib import Path

from common import LIB, REF_DIR, ffmpeg, load_json, probe, run, save_json

SCENE_THRESHOLD = 0.22
MIN_SHOT = 0.35


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_layout(path, w, h):
    """프레임 간 행별 변화량으로 '움직이는 영역(영상)'과 '고정 영역(헤드라인/배너)'을 구분."""
    import subprocess
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps=2,scale=1:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = [raw[i:i + h] for i in range(0, len(raw) - h + 1, h)]
    n = len(frames)
    var = []
    for y in range(h):
        vals = [f[y] for f in frames]
        m = sum(vals) / n
        var.append(sum((v - m) ** 2 for v in vals) / n)
    thr = max(var) * 0.02
    moving = [y for y, v in enumerate(var) if v > thr]
    if not moving:
        return {"media_y": 0, "media_h": h}
    # 가장 긴 연속 구간을 영상 영역으로
    best, cur = (moving[0], moving[0]), [moving[0], moving[0]]
    for y in moving[1:]:
        if y - cur[1] <= 3:
            cur[1] = y
        else:
            cur = [y, y]
        if cur[1] - cur[0] > best[1] - best[0]:
            best = tuple(cur)
    y0, y1 = best
    y0, y1 = y0 - y0 % 2, min(h, y1 + 1 + (y1 + 1) % 2)
    return {"headline_band": [0, y0], "media_y": y0, "media_h": y1 - y0, "banner_band": [y1, h]}


def detect_cuts(path, media_y, media_h, w, duration):
    r = run(["ffmpeg", "-hide_banner", "-i", path, "-vf",
             f"crop={w}:{media_h}:0:{media_y},select='gt(scene,{SCENE_THRESHOLD})',showinfo",
             "-f", "null", "-"])
    times = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    cuts = [0.0]
    for t in times:
        if t - cuts[-1] >= MIN_SHOT:
            cuts.append(t)
    if duration - cuts[-1] < MIN_SHOT and len(cuts) > 1:
        cuts.pop()
    cuts.append(duration)
    return list(zip(cuts[:-1], cuts[1:]))


SUB_BAND = (0.68, 0.92)   # 영상영역 안에서 자막이 놓이는 세로 구간(비율)


def detect_phrases(path, media_y, media_h, w, duration, fps=10):
    """자막 밴드의 변화 시점 = 자막 문구 전환. 참고영상의 실제 편집 단위(≈1초)."""
    import subprocess
    y0 = media_y + int(media_h * SUB_BAND[0])
    bh = int(media_h * (SUB_BAND[1] - SUB_BAND[0]))
    sw, sh = 48, 8
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"fps={fps},crop={w}:{bh}:0:{y0},scale={sw}:{sh},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    n = sw * sh
    frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
    cuts = [0.0]
    for i in range(1, len(frames)):
        diff = sum(abs(a - b) for a, b in zip(frames[i], frames[i - 1])) / n
        t = i / fps
        if diff > 14 and t - cuts[-1] >= 0.4:
            cuts.append(t)
    cuts.append(duration)
    return [(round(a, 2), round(b, 2)) for a, b in zip(cuts[:-1], cuts[1:])], (y0, bh)


_whisper = None


def transcribe(path):
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = _whisper.transcribe(str(path), language="ko", word_timestamps=True, vad_filter=False)
    words = []
    for s in segs:
        for wd in s.words or []:
            words.append({"t0": round(wd.start, 2), "t1": round(wd.end, 2), "w": wd.word.strip()})
    return words


def analyze(path, seen, group=None):
    path = Path(path).resolve()
    digest = md5(path)
    stem = path.stem
    vid = stem.split("_")[0] if stem.split("_")[0].isdigit() else re.sub(r"[^\w가-힣-]+", "_", stem)[:40]
    if digest in seen:
        print(f"[skip] {path.name} = 중복 ({seen[digest]})")
        return None
    seen[digest] = vid
    info = probe(path)
    out = LIB / "reference" / (group or "") / vid
    (out / "shots").mkdir(parents=True, exist_ok=True)
    layout = detect_layout(path, info["w"], info["h"])
    my, mh, w = layout["media_y"], layout["media_h"], info["w"]
    shots = detect_cuts(path, my, mh, w, info["duration"])
    print(f"[{vid}] {info['duration']:.1f}s, 영상영역 y={my} h={mh}, 컷 {len(shots)}개 → 전사 중...")
    words = transcribe(path)

    rows = []
    for i, (t0, t1) in enumerate(shots):
        clip = out / "shots" / f"{i:03d}.mp4"
        key = out / "shots" / f"{i:03d}.jpg"
        ffmpeg("-ss", f"{t0:.3f}", "-i", path, "-t", f"{t1 - t0:.3f}", "-vf", f"crop={w}:{mh}:0:{my}",
               "-an", "-c:v", "libx264", "-crf", "16", "-preset", "fast", clip)
        ffmpeg("-ss", f"{(t0 + t1) / 2:.3f}", "-i", path, "-frames:v", "1", "-q:v", "2", key)
        said = " ".join(x["w"] for x in words if x["t0"] < t1 and x["t1"] > t0 and (x["t0"] + x["t1"]) / 2 >= t0)
        rows.append({"idx": i, "t0": round(t0, 2), "t1": round(t1, 2), "dur": round(t1 - t0, 2),
                     "vo": said, "clip": clip.relative_to(LIB.parent).as_posix(),
                     "keyframe": key.relative_to(LIB.parent).as_posix(), "labels": None})

    # 번호 찍힌 컨택트시트
    ffmpeg("-i", path, "-vf",
           "select='" + "+".join(f"eq(n\\,{int(((a + b) / 2) * 30)})" for a, b in shots) + "',"
           "scale=180:-1,drawtext=text='%{n}':x=6:y=6:fontsize=22:fontcolor=yellow:box=1:boxcolor=black,"
           f"tile=10x{(len(shots) + 9) // 10}", "-frames:v", "1", "-fps_mode", "vfr", out / "sheet.jpg")

    # 자막 문구 단위 분할 + 자막 밴드만 모은 시트(에이전트가 자막 원문/색을 읽는 용도)
    phrases, (sy, sbh) = detect_phrases(path, my, mh, w, info["duration"])
    ffmpeg("-i", path, "-vf",
           "select='" + "+".join(f"eq(n\\,{int((a + min(b, a + 0.5)) / 2 * 30)})" for a, b in phrases) + "',"
           f"crop={w}:{sbh}:0:{sy},drawtext=text='%{{n}}':x=2:y=2:fontsize=16:fontcolor=yellow:box=1:boxcolor=black,"
           f"tile=3x{(len(phrases) + 2) // 3}", "-frames:v", "1", "-fps_mode", "vfr", out / "phrases.png")
    phrase_rows = []
    for i, (a, b) in enumerate(phrases):
        shot = next((s["idx"] for s in rows if s["t0"] <= a + 0.05 < s["t1"]), rows[-1]["idx"])
        said = " ".join(x["w"] for x in words if a <= (x["t0"] + x["t1"]) / 2 < b)
        phrase_rows.append({"idx": i, "t0": a, "t1": b, "dur": round(b - a, 2), "shot": shot,
                            "vo_asr": said, "text": None, "color": None, "style": None})

    vo_chars = sum(len(x["w"]) for x in words)
    data = {
        "id": vid, "group": group, "source": path.relative_to(LIB.parent).as_posix(), "md5": digest, **info,
        "layout": layout,
        "stats": {
            "shots": len(rows),
            "phrases": len(phrase_rows),
            "avg_phrase_sec": round(info["duration"] / max(1, len(phrase_rows)), 2),
            "avg_shot_sec": round(info["duration"] / max(1, len(rows)), 2),
            "vo_chars_per_sec": round(vo_chars / info["duration"], 2),
            "first_cut_sec": rows[1]["t0"] if len(rows) > 1 else None,
        },
        "headline": None,      # 에이전트가 채움: ["1줄", "2줄"] + 색
        "structure": None,     # 에이전트가 채움: HSO 구간 요약
        "words": words,
        "shots": rows,
        "phrases": phrase_rows,  # text/color/style 은 에이전트가 phrases.png 를 보고 채움
    }
    save_json(out / "analysis.json", data)
    print(f"[{vid}] 완료 → {out / 'analysis.json'}")
    return data


def build_index():
    """라벨링된 모든 컷을 하나의 검색용 카탈로그로."""
    items = []
    for a in sorted((LIB / "reference").rglob("analysis.json")):
        d = load_json(a)
        for s in d["shots"]:
            items.append({"ref": d["id"], "group": d.get("group"), "kind": "reference", **{k: s[k] for k in ("idx", "dur", "vo", "clip", "keyframe")},
                          "labels": s["labels"]})
    for a in sorted((LIB / "sources").glob("**/*.json")) + sorted((LIB / "generated").glob("**/*.json")):
        d = load_json(a)
        items.extend(d if isinstance(d, list) else [d])   # sources/<group>/catalog.json 은 목록
    save_json(LIB / "index.json", items)
    labeled = sum(1 for x in items if x.get("labels"))
    print(f"index.json: {len(items)}개 (라벨 완료 {labeled})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "index":
        build_index()
    else:
        args = sys.argv[2:]
        group = None
        if "--group" in args:          # 제품군별로 분리 저장: library/reference/<group>/<id>
            i = args.index("--group"); group = args[i + 1]; del args[i:i + 2]
        files = []
        for a in args or [REF_DIR]:
            a = Path(a)
            files += sorted(a.glob("*.mp4")) if a.is_dir() else [a]
        seen = {}
        for f in files:
            analyze(f, seen, group)
        build_index()
