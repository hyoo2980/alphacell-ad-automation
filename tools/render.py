"""EDL(편집계획 json) → 최종 광고 영상.

흐름: 문구별 TTS → 문구 길이에 맞춰 컷 트림/속도조절 → (필요 시) 기존 자막 제거 → 이어붙이기
      → 헤드라인·배너 합성 → 참고영상 스타일 자막(ASS) → 보이스+BGM 믹스/라우드니스 → mp4

  .venv/bin/python tools/render.py edl/v001.json [--preview]   # --preview: 540x960 빠른 시안

EDL 예시는 edl/_example.json 참고. line 필드:
  text   화면 자막 (필수)          vo     읽을 문장(자막과 다를 때: 숫자 읽기 등)
  style  box | outline | serif_story (기본 box)
  color  white|yellow|green|cyan|red|#RRGGBB (기본 스타일색)
  media  [{"src": 경로, "in": 시작초, "clean": "subtitle"|"crop"|null, "zoom": 1.0}]
         생략하면 직전 컷을 이어서 재생 (한 컷 위에 여러 문구)
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import ImageFont

from common import (OUT_DIR, ROOT, WORK, brand, color, ffmpeg, hex_to_ass, load_json, parse_rich, plain, probe,
                    save_json, set_profile, style)
from tts import synth_lines

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


# ---------- 폰트/텍스트 폭 ----------
def font_file(family, bold=True):
    from common import font_path
    return font_path(family, bold)


def text_width(text, family, size, bold, spacing):
    f = ImageFont.truetype(font_file(family, bold), size)
    return f.getlength(text) + spacing * max(0, len(text) - 1)


# ---------- 미디어 세그먼트 ----------
def clean_filter(mode, w, h):
    """참고영상 컷의 구워진 자막 처리. 원본 소스가 있으면 원본을 쓰는 게 항상 우선."""
    if mode == "subtitle":   # 자막 밴드(세로 62~95%)만 강하게 블러, 위아래 경계는 부드럽게 → 새 자막이 덮음
        y0, bh = int(h * 0.62), int(h * 0.33)
        y0, bh = y0 - y0 % 2, bh - bh % 2
        f = 14
        return (f"split[a][b];[b]crop={w}:{bh}:0:{y0},boxblur=luma_radius=18:luma_power=3,format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*clip(min(Y,{bh}-1-Y)/{f},0,1)'[bl];"
                f"[a]format=rgba[a2];[a2][bl]overlay=0:{y0},format=yuv420p")
    if mode == "crop":       # 자막 아래를 잘라내고 확대 (화질 손실 큼)
        return f"crop={w}:{int(h * 0.66)}:0:0"
    return None


def make_segment(m, start, dur, box, out, fps, nframes):
    """nframes 로 길이를 프레임 단위로 고정 → 누적 오차 없이 음성과 싱크."""
    src = ROOT / m["src"]
    W, H = box["w"], box["h"]
    zoom = m.get("zoom", 1.0)
    chain = []
    if src.suffix.lower() in IMG_EXT:
        # 정지 이미지: 천천히 확대(켄번스)
        frames = int(dur * fps) + 1
        inp = ["-loop", "1", "-t", f"{dur:.3f}", "-i", src]
        chain.append(f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                     f"zoompan=z='min({zoom}+0.0015*on,{zoom + 0.12})':d={frames}:s={W}x{H}:fps={fps}")
    else:
        info = probe(src)
        if info["duration"] - start < 0.3:          # 이어붙이다 컷이 끝났으면 뒷부분을 다시 사용
            start = max(0.0, info["duration"] - dur)
        avail = max(0.05, info["duration"] - start)
        inp = ["-ss", f"{start:.3f}", "-i", src]
        cf = clean_filter(m.get("clean"), info["w"], info["h"])
        if cf:
            chain.append(cf)
        speed = 1.0
        if avail < dur:
            speed = max(avail / dur, 0.5)          # 최대 2배 슬로우, 그래도 모자라면 마지막 프레임 정지
        chain.append(f"setpts=PTS/{speed:.4f}")
        fx, fy = m.get("focus_x", 0.5), m.get("focus_y", 0.5)   # 크롭 기준점(0=왼/위, 1=오른/아래)
        chain.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy}")
        if zoom != 1.0:
            chain.append(f"scale={int(W * zoom)}:{int(H * zoom)},crop={W}:{H}")
        chain.append(f"tpad=stop_mode=clone:stop_duration={dur:.3f}")
    chain.append(f"fps={fps},setsar=1,format=yuv420p")
    ffmpeg(*inp, "-filter_complex", ",".join(chain), "-frames:v", str(nframes), "-an",
           "-c:v", "libx264", "-crf", "15", "-preset", "fast", "-r", str(fps), out)


# ---------- 자막(ASS) ----------
def ass_style(name, s, fonts):
    fam = s.get("font", fonts["subtitle"])
    prim = hex_to_ass(color(s.get("color", "white")))
    if s.get("box"):
        # BorderStyle 3 = 불투명 박스. 박스색=OutlineColour, 여백=Outline
        return (f"Style: {name},{fam},{s['size']},{prim},{prim},{hex_to_ass(s['box_color'], s.get('box_alpha', 0))},"
                f"{hex_to_ass('#000000')},{-1 if s['bold'] else 0},{-1 if s.get('italic') else 0},0,0,100,100,{s['letter_spacing']},0,3,"
                f"{s['box_pad']},0,5,0,0,0,1")
    return (f"Style: {name},{fam},{s['size']},{prim},{prim},{hex_to_ass(s.get('outline_color', '#000000'))},"
            f"{hex_to_ass('#000000')},{-1 if s['bold'] else 0},{-1 if s.get('italic') else 0},0,0,100,100,{s['letter_spacing']},0,1,"
            f"{s.get('outline', 0)},{s.get('shadow', 0)},5,0,0,0,1")


def t_ass(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def rich_ass(text, boost=None):
    """{색|단어} 마크업 → ASS 색 태그. 강조 구간이 끝나면 스타일 기본색(\\r 대신 \\c 복원)으로."""
    out = []
    for seg, c in parse_rich(text):
        if c:
            h = hex_to_ass(color(c))
            b3 = "\\3c" + h if boost else ""
            out.append("{\\c" + h + b3 + "}" + seg + "{\\c&HFFFFFFFF&}")
        else:
            out.append(seg)
    return "".join(out)


def build_ass(edl, timeline, total, st, lay, path):
    fonts = st["fonts"]
    hs = st["headline"]
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {st['canvas']['w']}",
             f"PlayResY: {st['canvas']['h']}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding"]
    hl_outline = hs["outline_on_color_bg"] if edl.get("headline_bg") else hs["outline"]
    lines.append(ass_style("Headline", {"font": fonts["headline"], "size": hs["size"], "bold": hs["bold"],
                                        "italic": hs.get("italic", False),
                                        "letter_spacing": hs["letter_spacing"], "outline": hl_outline}, fonts))
    for n, s in st["subtitle_styles"].items():
        lines.append(ass_style(n, s, fonts))
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    cx = st["canvas"]["w"] // 2
    maxw = st["canvas"]["w"] - 60
    for i, hl in enumerate(edl.get("headline", [])):
        y = hs["line_y"][i] if lay["headline_band"] else 120 + i * 95
        size = hs["size"]
        w = text_width(plain(hl["text"]), fonts["headline"], size, True, hs["letter_spacing"]) * hs.get("scale_x", 100) / 100
        fs = f"\\fs{int(size * maxw / w)}" if w > maxw else ""
        c = hex_to_ass(color(hl.get("color", "white")))
        # 검정 밴드 위에서는 같은 색 얇은 외곽선으로 획을 굵게(참고영상의 두꺼운 헤드라인 서체 근사)
        use_boost = not edl.get("headline_bg")
        boost = f"\\bord{hs.get('weight_boost', 0)}\\3c{c}" if use_boost else ""
        body = rich_ass(hl["text"], use_boost).replace("\\c&HFFFFFFFF&", f"\\c{c}" + (f"\\3c{c}" if use_boost else ""))
        head = f"\\an5\\pos({cx},{y})\\fax-{hs.get('skew', 0)}\\fscx{hs.get('scale_x', 100)}"
        grad = hl.get("gradient") or (st["colors"].get(hl.get("color", "") + "_gradient"))
        if grad:   # 세로 그라데이션: 색 띠 여러 장을 \clip 으로 겹쳐 근사 (위→아래)
            n, top, h = 8, y - size * 0.6, size * 1.2
            c0, c1 = [tuple(int(g.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4)) for g in grad]
            for k in range(n):
                mix = "#%02X%02X%02X" % tuple(round(a + (b - a) * k / (n - 1)) for a, b in zip(c0, c1))
                ck = hex_to_ass(mix)
                b2 = f"\\bord{hs.get('weight_boost', 0)}\\3c{ck}" if use_boost else ""
                y0, y1 = int(top + h * k / n), int(top + h * (k + 1) / n) + 1
                lines.append(f"Dialogue: 1,{t_ass(0)},{t_ass(total)},Headline,,0,0,0,,"
                             f"{{{head}\\clip(0,{y0},{st['canvas']['w']},{y1})\\c{ck}{b2}{fs}}}{plain(hl['text'])}")
            continue
        lines.append(f"Dialogue: 1,{t_ass(0)},{t_ass(total)},Headline,,0,0,0,,"
                     f"{{{head}\\c{c}{boost}{fs}}}{body}")

    for ln, (t0, t1) in timeline:
        sname = ln.get("style", "box")
        s = st["subtitle_styles"][sname]
        fam = s.get("font", fonts["subtitle"])
        w = text_width(plain(ln["text"]), fam, s["size"], s["bold"], s["letter_spacing"]) + 2 * s.get("box_pad", 0)
        fs = f"\\fs{int(s['size'] * maxw / w)}" if w > maxw else ""
        base = hex_to_ass(color(ln.get("color") or s.get("color", "white")))
        body = rich_ass(ln["text"]).replace("\\c&HFFFFFFFF&", f"\\c{base}")
        lines.append(f"Dialogue: 2,{t_ass(t0)},{t_ass(t1)},{sname},,0,0,0,,"
                     f"{{\\an5\\pos({cx},{lay['subtitle_y']})\\c{base}{fs}}}{body}")
    # 고지 문구(연출·개인차) — 영상 영역 안 작은 글씨, 전체 구간
    dis = edl.get("disclaimer")
    if dis and lay.get("disclaimer"):
        d, ds = lay["disclaimer"], st.get("disclaimer_style", {})
        a = int(255 * (1 - ds.get("alpha", 0.6)))
        lines.append(f"Dialogue: 3,{t_ass(0)},{t_ass(total)},Headline,,0,0,0,,"
                     f"{{\\an7\\pos({d['x']},{d['y'] - d['size']})\\fs{d['size']}\\bord0\\b0\\fscx100"
                     f"\\c{hex_to_ass(ds.get('color', '#FFFFFF'))}\\alpha&H{a:02X}&}}{dis}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 자동 검수 ----------
def verify(out, segs, seg_times, box):
    """렌더 결과 자동 검사: ① 각 문구 구간에 의도한 컷이 나오는지(싱크) ② 검은 프레임."""
    def fr(path, t, crop):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                               crop + ",scale=32:32,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    h = int(box["h"] * 0.62)   # 자막 영역 제외
    ref = [fr(p, (b - a) / 2, f"crop={box['w']}:{h}:0:0") for p, (a, b) in zip(segs, seg_times)]
    off = []
    for i, (a, b) in enumerate(seg_times):
        q = fr(out, (a + b) / 2, f"crop={box['w']}:{h}:{box['x']}:{box['y']}")
        best = min(range(len(ref)), key=lambda j: sum(abs(x - y) for x, y in zip(q, ref[j])))
        if best != i and sum(abs(x - y) for x, y in zip(ref[best], ref[i])) > 0:
            off.append(i)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf",
                          f"crop={box['w']}:{box['h']}:{box['x']}:{box['y']},scale=16:16,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    dark = sum(1 for i in range(0, len(raw), 256) if sum(raw[i:i + 256]) / 256 < 12)
    print(f"[검수] 컷 싱크 {len(seg_times) - len(off)}/{len(seg_times)} 일치, 검은 프레임 {dark}개"
          + (f"  ⚠️ 불일치 컷: {off}" if off else ""))
    return not off and dark == 0


def verify_voice(timeline, wavs_raw):
    """문구별 음성을 받아써 자막과 비교. 옆 문구 음성이 섞였거나(분할 오류) 다른 말이면 경고."""
    import difflib
    import re
    from tts import _align_split  # noqa: F401  (Whisper 모델 공유)
    import tts
    from faster_whisper import WhisperModel
    if tts._whisper is None:
        tts._whisper = WhisperModel("small", device="cpu", compute_type="int8")
    norm = lambda t: re.sub(r"[^가-힣]", "", t)
    bad = []
    for i, ((ln, _), (wav, _)) in enumerate(zip(timeline, wavs_raw)):
        heard = " ".join(sg.text for sg in tts._whisper.transcribe(str(wav), language="ko")[0])
        want, got = norm(plain(ln["text"])), norm(heard)
        r = difflib.SequenceMatcher(None, want, got).ratio()
        if r < 0.5 or len(got) > len(want) * 1.6 + 2:
            bad.append((i, round(r, 2), plain(ln["text"]), heard.strip()))
    print(f"[검수] 음성-자막 {len(timeline) - len(bad)}/{len(timeline)} 일치" + (f"  ⚠️ 확인 필요: {bad}" if bad else ""))
    return not bad


# ---------- 메인 ----------
def render(edl_path, preview=False, qa=False, upload=False, force_upload=False, channel=None):
    """qa=True: 내부 화면 검수용. 임시 음성(say)으로 work/<id>/qa.mp4 만 만든다(납품물 아님, CapCut 생성 안 함)."""
    edl = load_json(edl_path)
    set_profile(edl.get("style_profile"))
    st = style()
    lay = st["layouts"][edl.get("layout", "band")]
    st["canvas"].update(lay.get("canvas", {}))
    if edl.get("disclaimer") is True:                       # true → 브랜드 기본 고지문
        edl["disclaimer"] = brand(edl.get("brand")).get("disclaimer")
    fps = st["canvas"]["fps"]
    gap = st["audio"]["line_gap_sec"]
    vid = edl["id"]
    wd = WORK / vid
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)

    # 1) 문구별 TTS → 타임라인
    voice = {"provider": "say", "voice": "Yuna"} if qa else edl.get("voice")
    if not qa and (voice or {}).get("provider") == "say":
        raise ValueError("납품 영상에 macOS 기본 음성(say)은 쓰지 않습니다 — gemini 또는 elevenlabs")
    res, add_gap = synth_lines([ln.get("vo", plain(ln["text"])) for ln in edl["lines"]], voice)
    t, timeline, wavs, wavs_raw = 0.0, [], [], []
    for ln, (wav, d) in zip(edl["lines"], res):
        dur = d + (gap if add_gap else 0) + ln.get("hold", 0)
        timeline.append((ln, (t, t + dur)))
        wavs.append((wav, dur))
        wavs_raw.append((wav, d))
        t += dur
    total = t

    # 2) 문구 길이에 맞춘 영상 세그먼트
    segs, seg_times, cur, cur_pos = [], [], None, 0.0
    for i, (ln, (t0, t1)) in enumerate(timeline):
        media = ln.get("media")
        if media:
            parts = media
            per = (t1 - t0) / len(parts)
            for j, m in enumerate(parts):
                out = wd / f"seg_{i:03d}_{j}.mp4"
                a, b = t0 + j * per, t0 + (j + 1) * per
                make_segment(m, m.get("in", 0), per, lay["media"], out, fps, round(b * fps) - round(a * fps))
                segs.append(out)
                seg_times.append((round(a * fps) / fps, round(b * fps) / fps))
            cur, cur_pos = parts[-1], parts[-1].get("in", 0) + per
        else:
            if cur is None:
                raise ValueError(f"line {i}: 첫 문구에는 media 가 필요합니다")
            out = wd / f"seg_{i:03d}_0.mp4"
            make_segment(cur, cur_pos, t1 - t0, lay["media"], out, fps, round(t1 * fps) - round(t0 * fps))
            segs.append(out)
            seg_times.append((round(t0 * fps) / fps, round(t1 * fps) / fps))
            cur_pos += t1 - t0
    (wd / "segs.txt").write_text("".join(f"file '{p}'\n" for p in segs))
    media_mp4 = wd / "media.mp4"
    # concat 필터: 세그먼트마다 PTS 를 0 부터 다시 매겨 이어붙임 (demuxer copy 는 이미지 세그먼트 등에서 싱크가 밀림)
    ins, fl = [], []
    for k, p in enumerate(segs):
        ins += ["-i", p]
        fl.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p[s{k}]")
    fl.append("".join(f"[s{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]")
    ffmpeg(*ins, "-filter_complex", ";".join(fl), "-map", "[v]", "-c:v", "libx264", "-crf", "14",
           "-preset", "fast", "-r", str(fps), media_mp4)

    # 3) 보이스 트랙
    voice = wd / "voice.wav"
    inputs, flt = [], []
    for k, (wav, dur) in enumerate(wavs):
        inputs += ["-i", wav]
        flt.append(f"[{k}]apad=whole_dur={dur:.3f}[a{k}]")
    flt.append("".join(f"[a{k}]" for k in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1[out]")
    ffmpeg(*inputs, "-filter_complex", ";".join(flt), "-map", "[out]", "-ar", "48000", voice)

    # 4) 자막
    ass = wd / "subs.ass"
    build_ass(edl, timeline, total, st, lay, ass)

    # 5) 합성
    cw, ch = st["canvas"]["w"], st["canvas"]["h"]
    args = ["-f", "lavfi", "-i", f"color=c=black:s={cw}x{ch}:r={fps}:d={total:.3f}", "-i", media_mp4]
    fc = [f"[1]setpts=PTS-STARTPTS[m];[0][m]overlay={lay['media']['x']}:{lay['media']['y']}:eof_action=repeat[v0]"]
    last, idx = "v0", 2
    if edl.get("headline_bg") and lay["headline_band"]:
        hb = lay["headline_band"]
        args += ["-i", ROOT / edl["headline_bg"]]
        fc.append(f"[{idx}]scale={cw}:{hb['h']}[hb];[{last}][hb]overlay=0:{hb['y']}[v{idx}]")
        last, idx = f"v{idx}", idx + 1
    if edl.get("banner") and lay["banner"]:
        b = lay["banner"]
        args += ["-i", ROOT / edl["banner"]]
        fc.append(f"[{idx}]scale={b['w']}:{b['h']}:force_original_aspect_ratio=increase,crop={b['w']}:{b['h']}[bn];"
                  f"[{last}][bn]overlay={b['x']}:{b['y']}[v{idx}]")
        last, idx = f"v{idx}", idx + 1
    from common import fonts_dir_for_ass
    fontsdir = fonts_dir_for_ass().replace("\\", "/").replace(":", "\\:")   # Windows 드라이브 콜론 이스케이프
    scale = ",scale=540:960" if preview else ""
    fc.append(f"[{last}]ass='{ass}':fontsdir='{fontsdir}'{scale}[vout]")

    args += ["-i", voice]
    vi = idx
    au = st["audio"]
    if edl.get("bgm"):
        args += ["-stream_loop", "-1", "-i", ROOT / edl["bgm"]]
        bi = idx + 1
        fc.append(f"[{vi}]volume={au['voice_gain_db']}dB,asplit[vo][sc]")
        fc.append(f"[{bi}]volume={edl.get('bgm_gain_db', au['bgm_gain_db'])}dB,atrim=0:{total:.3f}[bg]")
        if au["duck"]:
            fc.append("[bg][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=300[bgd]")
            fc.append("[vo][bgd]amix=inputs=2:normalize=0[mix]")
        else:
            fc.append("[vo][bg]amix=inputs=2:normalize=0[mix]")
        fc.append(f"[mix]loudnorm=I={au['target_lufs']}:TP=-1.0:LRA=11[aout]")
    else:
        fc.append(f"[{vi}]loudnorm=I={au['target_lufs']}:TP=-1.0:LRA=11[aout]")

    OUT_DIR.mkdir(exist_ok=True)
    out = wd / "qa.mp4" if qa else OUT_DIR / f"{vid}{'_preview' if preview else ''}.mp4"
    ffmpeg(*args, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
           "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "28" if preview else "18", "-preset", "medium",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", out)
    save_json((wd if qa else OUT_DIR) / f"{vid}.timeline.json",
              {"edl": str(edl_path), "duration": round(total, 2),
               "lines": [{"t0": round(a, 2), "t1": round(b, 2), "text": ln["text"]} for ln, (a, b) in timeline]})
    # CapCut 내보내기(capcut.py)가 쓰는 편집 정보: 어떤 파일이 타임라인 어디에 들어갔는지
    save_json(wd / "manifest.json", {
        "id": vid, "edl": str(Path(edl_path).resolve()), "duration": total, "fps": fps,
        "style_profile": edl.get("style_profile"), "disclaimer": edl.get("disclaimer"),
        "layout": edl.get("layout", "band"), "headline": edl.get("headline", []),
        "banner": edl.get("banner"), "headline_bg": edl.get("headline_bg"),
        "bgm": edl.get("bgm"), "bgm_gain_db": edl.get("bgm_gain_db", au["bgm_gain_db"]),
        "segments": [{"path": str(p), "t0": a, "t1": b} for p, (a, b) in zip(segs, seg_times)],
        "lines": [{"text": ln["text"], "style": ln.get("style", "box"), "color": ln.get("color"),
                   "t0": a, "t1": b, "wav": str(w), "wav_dur": d}
                  for (ln, (a, b)), (w, d) in zip(timeline, wavs_raw)],
        "output": str(out)})
    print(f"완료 → {out} ({total:.1f}s, 문구 {len(timeline)}개)")
    ok = True
    if not preview:
        ok = verify(out, segs, seg_times, lay["media"])
        if not qa:
            ok = verify_voice(timeline, wavs_raw) and ok
    up_rc = None
    if upload and not qa and not preview:
        if not channel:
            raise ValueError("--upload 에는 --channel <채널id> 가 필요합니다")
        if not ok:
            # 무인 운영: 자동 검수 실패 영상은 절대 올리지 않는다 (EDL 을 고쳐 다시 렌더)
            print("[업로드 안 함] 자동 검수 실패 → EDL 수정 후 다시 렌더하세요")
            return out, ok, None
        from check import risky
        from queue_upload import queue
        ucfg = load_json(ROOT / "upload" / "config.json")
        hits = risky(edl_path)
        if hits and ucfg.get("block_risky", True) and not force_upload:
            dst = queue(wd / "manifest.json", channel, folder="hold")
            (dst.with_suffix(".HOLD.txt")).write_text(
                f"자동 업로드 보류: 위험 표현 {hits}\n", encoding="utf-8")
            print(f"[업로드 보류] 위험 표현 {hits} → upload/channels/{channel}/hold/")
        else:
            queue(wd / "manifest.json", channel)
            up_rc = subprocess.run([sys.executable, str(ROOT / "upload/scripts/upload_daily.py"),
                                    "--channel", channel]).returncode
    return out, ok, up_rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("edl")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--qa", action="store_true", help="내부 화면 검수용(macOS 전용 임시 음성, work/ 에만 저장)")
    ap.add_argument("--upload", action="store_true", help="검수 통과 시 채널 대기열 등록 + 즉시 예약 업로드")
    ap.add_argument("--channel", help="업로드할 채널 id (upload/channels.json)")
    ap.add_argument("--force-upload", action="store_true", help="위험 표현이 있어도 업로드(사람이 확인한 경우만)")
    a = ap.parse_args()
    _, ok, rc = render(a.edl, a.preview, a.qa, a.upload, a.force_upload, a.channel)
    # 종료 코드: 검수 실패 1, 업로드 스크립트 오류는 그 코드(2 로그인 만료, 3 쿼터, 4 업로드 실패)
    sys.exit(1 if not ok else (rc or 0))
