"""정보형 광고 쇼츠 렌더러 (EDL "format": "info") — 레이아웃·음성 규격은 CLAUDE.md "정보형 쇼츠" 절.

  python tools/render.py edl/<ch>/<id>.json --channel <ch> --upload   (render.py 가 format 을 보고 여기로 넘긴다)

화면: 짙은 회색 배경(노이즈+비네팅) / 상단 부제(흰 50px) / 메인 헤드라인(형광초록 118px, 1~2줄) /
      영상 박스 1080x720 @y640 + 좌상단 주제 라벨 / 박스 아래 자막(흰 70px, 15자 안팎 2~3줄, 강조 노랑) /
      하단 고지 / 마지막 4.5초 보라 CTA 카드.
음성: Google Cloud TTS, 자막 한 줄씩 합성해 이어 붙임(줄 0.18초, 장면 0.35초, 끝 0.6초). 60초 넘으면 속도 1.13 으로 재합성.
"""
import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import ImageFont

from common import OUT_DIR, WORK, ffmpeg, hex_to_ass, load_env, load_json, parse_rich, plain, probe, save_json

W, H, FPS = 1080, 1920, 30
BOX = {"x": 0, "y": 640, "w": 1080, "h": 720}
GREEN, YELLOW, NAVY, GRAY = "#8CF542", "#FFE135", "#12305C", "#A8A8A8"
GAP_LINE, GAP_SCENE, TAIL = 0.18, 0.35, 0.6
DISCLAIMER = "정보 제공 목적이며 개인차가 있을 수 있습니다"
CTA_TOP = "식약처 인정 기능성 원료 · 귀리 식이섬유"
CTA_BRAND = "알파셀 혈당 세이프"
CTA_SUB = "밥에 톡톡 뿌려 드시는 혈당 관리"
CTA_BTN = "'알파셀 혈당 세이프'를 검색해보세요!"
CACHE = WORK / "tts_cache_info"


# ---------- 글꼴: 로컬 윈도우는 맑은 고딕 Bold, 서버(리눅스)는 Noto Sans CJK KR Bold ----------
def font():
    for fam, path, idx in [("Malgun Gothic", "C:/Windows/Fonts/malgunbd.ttf", 0),
                           ("Noto Sans CJK KR", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1)]:
        if Path(path).exists():
            return fam, path, idx
    raise FileNotFoundError("맑은 고딕 / Noto Sans CJK 글꼴이 없습니다 (서버: fonts-noto-cjk 설치)")


def text_w(text, size):
    _, path, idx = font()
    return ImageFont.truetype(path, size, index=idx).getlength(text)


# ---------- 음성 ----------
def google_tts(text, voice, rate, out):
    import requests
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_TTS_API_KEY 없음")
    r = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize", params={"key": key}, json={
        "input": {"text": text},
        "voice": {"languageCode": "ko-KR", "name": voice},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000, "speakingRate": rate},
    }, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Google TTS {r.status_code}: {r.text[:300]}")
    out.write_bytes(base64.b64decode(r.json()["audioContent"]))


def synth_line(text, v, rate, out):
    """한 줄 음성. Google 이 안 되면(키 없음 등) 같은 줄을 Edge 음성으로(경고)."""
    try:
        google_tts(text, v.get("voice", "ko-KR-Neural2-C"), rate, out)
    except Exception as e:
        if not getattr(synth_line, "_warned", False):
            print(f"::warning::Google TTS 사용 불가 → Edge 음성으로 대체 ({str(e)[:150]})")
            synth_line._warned = True
        import tts
        tts._edge_call(text, {"voice": "ko-KR-InJoonNeural", "rate": f"+{int((rate - 1) * 100)}%"}, out)


def voice_track(lines, v, rate, wd):
    wd.mkdir(parents=True, exist_ok=True)
    wavs, durs = [], []
    for i, ln in enumerate(lines):
        raw, wav = wd / f"l{i:02d}.raw.wav", wd / f"l{i:02d}.wav"
        synth_line(ln.get("vo") or plain(ln["text"]), v, rate, raw)
        ffmpeg("-i", raw, "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
               "silenceremove=start_periods=1:start_threshold=-45dB,areverse", "-ar", "48000", "-ac", "1", wav)
        wavs.append(wav)
        durs.append(probe(wav)["duration"])
    return wavs, durs


# ---------- 자막 줄바꿈 (15자 안팎, 최대 3줄) ----------
def wrap_rich(text, width=15, max_lines=3):
    words = []
    for seg, c in parse_rich(text):
        for k, w in enumerate(re.split(r"(\s+)", seg)):
            if w and not w.isspace():
                words.append((w, c))
    lines, cur, n = [], [], 0
    for w, c in words:
        if cur and n + len(w) > width and len(lines) < max_lines - 1:
            lines.append(cur)
            cur, n = [], 0
        cur.append((w, c))
        n += len(w) + 1
    if cur:
        lines.append(cur)
    return lines


def rich_line(tokens, base):
    out = []
    for w, c in tokens:
        out.append("{\\c" + hex_to_ass(YELLOW) + "}" + w + "{\\c" + base + "}" if c else w)
    return " ".join(out)


def t_ass(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_ass(edl, timeline, total, path):
    fam = font()[0]
    white, blk = hex_to_ass("#FFFFFF"), hex_to_ass("#000000")
    L = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
         "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
         "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
         "MarginV, Encoding",
         f"Style: T,{fam},50,{white},{white},{blk},{blk},-1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1",
         f"Style: H,{fam},118,{hex_to_ass(GREEN)},{white},{blk},{blk},-1,0,0,0,100,100,-2,0,1,7,0,5,0,0,0,1",
         f"Style: Lab,{fam},34,{white},{white},{hex_to_ass(NAVY)},{blk},-1,0,0,0,100,100,0,0,3,10,0,7,0,0,0,1",
         f"Style: S,{fam},70,{white},{white},{blk},{blk},-1,0,0,0,100,100,-1,0,1,5,0,8,0,0,0,1",
         f"Style: D,{fam},34,{hex_to_ass(GRAY)},{white},{blk},{blk},0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1",
         f"Style: C,{fam},40,{white},{white},{blk},{blk},-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
         f"Style: P,{fam},40,{white},{white},{blk},{blk},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
         "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = lambda layer, a, b, st, txt: L.append(f"Dialogue: {layer},{t_ass(a)},{t_ass(b)},{st},,0,0,0,,{txt}")

    def fit(text, size, maxw=1020):
        w = text_w(text, size)
        return f"\\fs{int(size * maxw / w)}" if w > maxw else ""

    if edl.get("subtitle_top"):
        ev(1, 0, total, "T", f"{{\\pos(540,250){fit(edl['subtitle_top'], 50)}}}{edl['subtitle_top']}")
    hl = [h for h in (edl.get("headline") or [])[:2] if h]
    ys = [465] if len(hl) == 1 else [400, 530]
    for h, y in zip(hl, ys):
        ev(1, 0, total, "H", f"{{\\pos(540,{y}){fit(plain(h), 118)}}}{plain(h)}")
    if edl.get("topic_label"):
        ev(2, 0, total, "Lab", f"{{\\pos({BOX['x'] + 24},{BOX['y'] + 22})}}{edl['topic_label']}")
    for ln, (a, b) in timeline:
        body = "\\N".join(rich_line(t, white) for t in wrap_rich(ln["text"]))
        ev(3, a, b, "S", f"{{\\pos(540,1420)}}{body}")
    ev(3, 0, total, "D", f"{{\\pos(540,1880)}}{DISCLAIMER}")
    # 엔딩 CTA 카드 (마지막 4.5초, 영상 박스 위, 페이드인)
    a = max(0.0, total - 4.5)
    x0, y0, bw, bh = BOX["x"], BOX["y"], BOX["w"], BOX["h"]
    c0, c1, n = (0x4A, 0x24, 0x78), (0x20, 0x0E, 0x3A), 12
    for k in range(n):   # 세로 그라데이션: 띠 12장
        col = "#%02X%02X%02X" % tuple(round(p + (q - p) * k / (n - 1)) for p, q in zip(c0, c1))
        t0, t1 = y0 + bh * k // n, y0 + bh * (k + 1) // n + 1
        ev(4, a, total, "P", f"{{\\an7\\pos({x0},{t0})\\fad(400,0)\\bord0\\shad0\\c{hex_to_ass(col)}\\p1}}"
                             f"m 0 0 l {bw} 0 {bw} {t1 - t0} 0 {t1 - t0}{{\\p0}}")
    cx = x0 + bw // 2
    ev(5, a, total, "C", f"{{\\pos({cx},{y0 + 170})\\fad(400,0)\\fs36\\c{hex_to_ass('#E6D6FF')}}}{CTA_TOP}")
    ev(5, a, total, "C", f"{{\\pos({cx},{y0 + 290})\\fad(400,0)\\fs104{fit(CTA_BRAND, 104)}}}{CTA_BRAND}")
    ev(5, a, total, "C", f"{{\\pos({cx},{y0 + 400})\\fad(400,0)\\fs44}}{CTA_SUB}")
    pw, ph = 920, 112
    px, py = cx - pw // 2, y0 + 500
    r = ph // 2
    pill = (f"m {r} 0 l {pw - r} 0 b {pw} 0 {pw} {ph} {pw - r} {ph} l {r} {ph} b 0 {ph} 0 0 {r} 0")
    ev(5, a, total, "P", f"{{\\an7\\pos({px},{py})\\fad(400,0)\\bord0\\shad0\\c{hex_to_ass(YELLOW)}\\p1}}{pill}{{\\p0}}")
    ev(6, a, total, "C", f"{{\\pos({cx},{py + ph // 2})\\fad(400,0)\\fs44\\c{blk}{fit(CTA_BTN, 44, pw - 60)}}}{CTA_BTN}")
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------- 메인 ----------
def render_info(edl_path):
    """반환: (출력 mp4, manifest 경로)"""
    from render import make_segment
    load_env()
    edl = load_json(edl_path)
    vid = edl["id"]
    wd = WORK / vid
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    v = edl.get("voice") or {}
    lines = edl["lines"]

    rate = float(v.get("rate", 1.08))
    for attempt in range(2):
        wavs, durs = voice_track(lines, v, rate, wd / f"tts_{attempt}")
        gaps = [GAP_SCENE if (i + 1 < len(lines) and lines[i + 1].get("scene_break")) else GAP_LINE
                for i in range(len(lines))]
        gaps[-1] = TAIL
        total = sum(durs) + sum(gaps)
        if total <= 60 or rate >= 1.13:
            break
        rate = 1.13   # 60초 초과 → 1.13 으로 재합성
    timeline, t = [], 0.0
    for ln, d, g in zip(lines, durs, gaps):
        timeline.append((ln, (t, t + d + g)))
        t += d + g
    total = t

    # 영상: 줄마다 지정 컷 (여러 개면 균등 분할, 모자라면 슬로우→정지 — 반복 재생 없음)
    segs, cur = [], None
    for i, (ln, (t0, t1)) in enumerate(timeline):
        media = ln.get("media") or ([cur] if cur else None)
        if not media:
            raise ValueError(f"line {i}: media 없음")
        per = (t1 - t0) / len(media)
        for j, m in enumerate(media):
            a, b = t0 + j * per, t0 + (j + 1) * per
            out = wd / f"seg_{i:03d}_{j}.mp4"
            make_segment(m, m.get("in", 0), per, BOX, out, FPS, round(b * FPS) - round(a * FPS))
            segs.append(out)
        cur = {**media[-1], "in": media[-1].get("in", 0) + per}
    ins, fl = [], []
    for k, p in enumerate(segs):
        ins += ["-i", p]
        fl.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[s{k}]")
    fl.append("".join(f"[s{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]")
    media_mp4 = wd / "media.mp4"
    ffmpeg(*ins, "-filter_complex", ";".join(fl), "-map", "[v]", "-c:v", "libx264", "-crf", "16", "-preset", "fast",
           "-r", str(FPS), media_mp4)

    # 보이스: 줄 음성 + 쉼
    ins, fl = [], []
    for k, (wav, (ln, (a, b))) in enumerate(zip(wavs, timeline)):
        ins += ["-i", wav]
        fl.append(f"[{k}]apad=whole_dur={b - a:.3f}[a{k}]")
    fl.append("".join(f"[a{k}]" for k in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1[out]")
    voice = wd / "voice.wav"
    ffmpeg(*ins, "-filter_complex", ";".join(fl), "-map", "[out]", "-ar", "48000", voice)

    ass = wd / "subs.ass"
    build_ass(edl, timeline, total, ass)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{vid}.mp4"
    assp = str(ass).replace("\\", "/").replace(":", "\\:")
    fc = (f"[0]noise=alls=7:allf=t+u,vignette=PI/4.5[bg];[1]setpts=PTS-STARTPTS[m];"
          f"[bg][m]overlay={BOX['x']}:{BOX['y']}:eof_action=repeat,ass='{assp}'[vout];"
          f"[2]loudnorm=I=-14:TP=-1.0:LRA=11[aout]")
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x1E1E1E:s={W}x{H}:r={FPS}:d={total:.3f}", "-i", media_mp4, "-i", voice,
           "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out)
    mf = wd / "manifest.json"
    save_json(mf, {"id": vid, "edl": str(Path(edl_path).resolve()), "duration": total, "output": str(out),
                   "headline": [{"text": h} for h in edl.get("headline", [])], "disclaimer": DISCLAIMER,
                   "lines": [{"text": ln["text"]} for ln in lines], "format": "info"})
    print(f"완료 → {out} ({total:.1f}s, 문구 {len(lines)}개, 음성 속도 {rate})")
    return out, mf
