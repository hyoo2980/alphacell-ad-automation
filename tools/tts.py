"""TTS. 문구 리스트 → 문구별 wav + 길이. render.py 가 이 길이로 자막 타이밍을 잡는다.

voice 설정 (EDL "voice" 또는 .env 기본값):
  {"provider": "elevenlabs",          # say | elevenlabs | google
   "voice": "<voice_id>",             # 생략 시 ELEVENLABS_VOICE_ID
   "model": "eleven_v3",              # 생략 시 ELEVENLABS_MODEL
   "mode": "script",                  # script: 대본 전체를 1회 생성 후 글자 타임스탬프로 문구 분할(톤 일관, v3 권장)
                                      # line:   문구마다 따로 생성
   "stability": 0.5,                  # v3: 0.0 Creative / 0.5 Natural / 1.0 Robust 로 자동 스냅
   "similarity": 0.75, "speed": 1.0,  # speed 는 API 파라미터(0.7~1.2)
   "tempo": 1.0}                      # tempo 는 생성 후 ffmpeg 로 속도 보정(모든 엔진 공통)

v3 감정 태그는 vo 에 그대로 쓰면 됨: {"text": "지금 바로 확인하세요!", "vo": "[excited] 지금 바로 확인하세요!"}

  .venv/bin/python tools/tts.py "잇몸이 내려앉고 있다면" --provider elevenlabs --model eleven_v3
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import requests

from common import WORK, ffmpeg, load_env, probe

CACHE = WORK / "tts_cache"
XI = "https://api.elevenlabs.io/v1/text-to-speech"
TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02,"
        "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse")


def xi_accounts():
    """ELEVENLABS_ACCOUNTS(JSON 목록) → [{"key","voice_id"}]. 없으면 단일 키 설정을 한 개짜리 목록으로."""
    accs = []
    # 처음 단독으로 등록한 계정(ELEVENLABS_API_KEY/VOICE_ID)이 1번, 이후 추가 등록한 목록이 뒤에 붙는다
    if os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_VOICE_ID"):
        accs.append({"key": os.environ["ELEVENLABS_API_KEY"], "voice_id": os.environ["ELEVENLABS_VOICE_ID"]})
    raw = os.environ.get("ELEVENLABS_ACCOUNTS", "").strip()
    if raw:
        try:
            accs += [a for a in json.loads(raw) if a.get("key") and a.get("voice_id")
                     and a["key"] not in {x["key"] for x in accs}]
        except Exception:
            print("::warning::ELEVENLABS_ACCOUNTS 형식 오류")
    return accs


def voice_cfg(v=None):
    """EDL voice + .env 기본값 병합."""
    load_env()
    v = dict(v or {})
    v.setdefault("provider", os.environ.get("TTS_PROVIDER", "say"))
    if v["provider"] == "elevenlabs" and not (xi_accounts() or (os.environ.get("ELEVENLABS_API_KEY") and
                                              (v.get("voice") or os.environ.get("ELEVENLABS_VOICE_ID")))):
        print("::warning::ElevenLabs 키/보이스 ID 없음 → Gemini 로 대체 (setup_secrets.bat 로 등록)")
        v = {"provider": "gemini", "voice": "Puck"}
    if v["provider"] == "elevenlabs":
        v.setdefault("voice", os.environ.get("ELEVENLABS_VOICE_ID"))
        v.setdefault("model", os.environ.get("ELEVENLABS_MODEL", "eleven_v3"))
        v.setdefault("mode", "script" if v["model"] == "eleven_v3" else "line")
        v.setdefault("stability", float(os.environ.get("ELEVENLABS_STABILITY", 0.5)))
        v.setdefault("similarity", float(os.environ.get("ELEVENLABS_SIMILARITY", 0.75)))
        v.setdefault("speed", float(os.environ.get("ELEVENLABS_SPEED", 1.0)))
        if not v["voice"]:
            raise ValueError("ElevenLabs voice_id 가 없습니다 (.env ELEVENLABS_VOICE_ID 또는 EDL voice.voice)")
    if v["provider"] == "edge":
        v.setdefault("voice", "ko-KR-InJoonNeural")
        v.setdefault("rate", "+12%")
        v.setdefault("model", "edge")
        v.setdefault("style", "")
        v.setdefault("mode", "script")
    if v["provider"] == "gemini":
        v.setdefault("voice", os.environ.get("GEMINI_TTS_VOICE", "Puck"))
        v.setdefault("model", os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"))
        v.setdefault("style", os.environ.get("GEMINI_TTS_STYLE",
                     "홈쇼핑 광고 내레이터처럼 빠르고 힘 있고 확신에 찬 톤으로 읽어 주세요"))
        v.setdefault("mode", "script")
    v.setdefault("mode", "line")
    v.setdefault("tempo", 1.0)
    return v


def _key(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


# ---------------- 엔진별 합성 ----------------
def _synth_say(text, v, out):
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", v.get("voice") or "Yuna", "-r", "230", "-o", str(aiff), text], check=True)
    ffmpeg("-i", aiff, "-ar", "48000", "-ac", "1", out)
    aiff.unlink()


def _xi_settings(v):
    st = v["stability"]
    if v["model"] == "eleven_v3":           # v3 는 3단계만 허용
        st = min((0.0, 0.5, 1.0), key=lambda x: abs(x - st))
    return {"stability": st, "similarity_boost": v["similarity"], "speed": v["speed"]}


def _xi_post(path, v, body):
    """v3 에서 지원하지 않는 설정이 있으면 400 → 최소 설정으로 재시도."""
    headers = {"xi-api-key": v.get("api_key") or os.environ["ELEVENLABS_API_KEY"]}
    fmt = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")   # 192k 는 Creator 요금제 이상
    url = f"{XI}/{v['voice']}{path}?output_format={fmt}"
    base = {"model_id": v["model"], **body}
    if v["model"] != "eleven_multilingual_v2":
        base["language_code"] = v.get("language", "ko")
    r = requests.post(url, headers=headers, json={**base, "voice_settings": _xi_settings(v)}, timeout=300)
    if r.status_code == 400:
        r = requests.post(url, headers=headers,
                          json={**base, "voice_settings": {"stability": _xi_settings(v)["stability"]}}, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:500]}")
    return r


def _eleven_call(text, v, out):
    """ElevenLabs 대본 1회 생성(크레딧 = 글자 수). 문구 분할은 Whisper 정렬로 한다."""
    import re
    text = re.sub(r"\s+", " ", text)
    r = _xi_post("", v, {"text": text})
    mp3 = Path(str(out) + ".mp3")
    mp3.write_bytes(r.content)
    ffmpeg("-i", mp3, "-ar", "24000", "-ac", "1", out)
    mp3.unlink()


def _synth_elevenlabs(text, v, out):
    r = _xi_post("", v, {"text": text})
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    ffmpeg("-i", mp3, "-ar", "48000", "-ac", "1", out)
    mp3.unlink()


def _synth_google(text, v, out):
    key = os.environ["GOOGLE_TTS_API_KEY"]
    r = requests.post(f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}", json={
        "input": {"text": text},
        "voice": {"languageCode": "ko-KR", "name": v.get("voice") or "ko-KR-Neural2-C"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000},
    }, timeout=120)
    r.raise_for_status()
    out.write_bytes(base64.b64decode(r.json()["audioContent"]))


def _gemini_call(text, v, out):
    """Gemini TTS (무료 등급 사용 가능). 스타일 지시는 텍스트 앞에 붙임(모델이 읽지 않음). 429/500 은 재시도."""
    import time
    key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{v['model']}:generateContent"
    body = {"contents": [{"parts": [{"text": f"{v['style']}: {text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v["voice"]}}}}}
    for attempt in range(3):
        # 키는 헤더로 전송: 옛 형식(AIza…)·새 형식(AQ.…) 키 모두 동작
        r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=300)
        if r.ok:
            break
        if r.status_code in (429, 500, 503):
            wait = 20 * (attempt + 1)
            print(f"[tts] Gemini {r.status_code} → {wait}초 후 재시도")
            time.sleep(wait)
            continue
        raise RuntimeError(f"Gemini TTS {r.status_code}: {r.text[:300]}")
    else:
        raise RuntimeError(f"Gemini TTS 재시도 초과: {r.status_code} {r.text[:200]}")
    part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    raw = base64.b64decode(part["data"])
    if "wav" in part.get("mimeType", ""):
        out.write_bytes(raw)
    else:  # audio/L16;rate=24000 (raw PCM)
        rate = next((x.split("=")[1] for x in part["mimeType"].split(";") if x.startswith("rate=")), "24000")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", rate, "-ac", "1", "-i", "-", str(out)],
                       input=raw, check=True)


# ---------------- Edge TTS (무료, 키 불필요) — Gemini 한도 초과 시 자동 대체 ----------------
EDGE_VOICES = {"Puck": "ko-KR-InJoonNeural", "Orus": "ko-KR-HyunsuNeural", "Fenrir": "ko-KR-InJoonNeural",
               "Charon": "ko-KR-HyunsuNeural", "Sadachbia": "ko-KR-SunHiNeural"}


EDGE_TO_GEMINI = {"ko-KR-InJoonNeural": "Puck", "ko-KR-HyunsuMultilingualNeural": "Orus", "ko-KR-SunHiNeural": "Sadachbia"}


def _edge_call(text, v, out):
    """Microsoft Edge 온라인 음성(edge-tts). 광고 톤에 맞게 약간 빠르게."""
    import asyncio
    import edge_tts
    vo = v.get("voice") or ""
    voice = vo if vo.startswith("ko-KR") else EDGE_VOICES.get(vo, "ko-KR-InJoonNeural")
    mp3 = Path(str(out) + ".mp3")
    asyncio.run(edge_tts.Communicate(text, voice, rate=v.get("rate") or "+12%").save(str(mp3)))
    ffmpeg("-i", mp3, "-ar", "24000", "-ac", "1", out)
    mp3.unlink()


def _synth_gemini(text, v, out):
    _gemini_call(text, v, out)


_whisper = None


def _align_split(audio, texts):
    """대본 전체 오디오를 Whisper 단어 타임스탬프 + 글자 정렬로 문구별 경계를 찾는다. 반환: 컷 시점 목록."""
    import difflib
    import re
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel("small", device="cpu", compute_type="int8")
    def num_kor(n):
        d, u, out = "영일이삼사오육칠팔구", ["", "십", "백", "천"], ""
        if n == 0:
            return "영"
        for k, ch in enumerate(reversed(str(n))):
            v = int(ch)
            if v:
                out = ("" if (v == 1 and k > 0) else d[v]) + u[k] + out
        return out

    def speak(t):                     # Whisper 는 숫자를 '62%' 로 적음 → 대본의 '육십이 퍼센트' 와 맞추기
        t = re.sub(r"(\d+)\s*%", lambda m: num_kor(int(m.group(1))) + "퍼센트", t)
        return re.sub(r"\d{1,4}", lambda m: num_kor(int(m.group(0))), t)
    norm = lambda t: re.sub(r"[^0-9A-Za-z가-힣]", "", speak(t))
    segs, _ = _whisper.transcribe(str(audio), language="ko", word_timestamps=True,
                                  initial_prompt=" ".join(texts)[:400])
    wc, wt = [], []                       # Whisper 글자 + 글자별 시각(단어 구간을 글자수로 균등 분배)
    for sg in segs:
        for w in sg.words or []:
            cs = norm(w.word)
            for k, ch in enumerate(cs):
                wc.append(ch)
                wt.append(w.start + (w.end - w.start) * (k + 0.5) / len(cs))
    S = "".join(norm(t) for t in texts)
    t_of = {}
    for blk in difflib.SequenceMatcher(None, S, "".join(wc), autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            t_of[blk.a + k] = wt[blk.b + k]
    if len(t_of) < len(S) * 0.6:
        raise RuntimeError(f"정렬 실패(일치 {len(t_of)}/{len(S)}글자)")
    known = sorted(t_of)
    cuts, pos = [], 0
    for t in texts[:-1]:
        pos += len(norm(t))
        before = [i for i in known if i < pos]
        after = [i for i in known if i >= pos]
        if not before or not after:
            raise RuntimeError("정렬 실패(경계 없음)")
        cuts.append((t_of[before[-1]] + t_of[after[0]]) / 2)
    return cuts


def synth_script_gemini(texts, v, engine="gemini"):
    """대본 전체 1회 생성 → Whisper 정렬로 문구 분할. engine: gemini | edge"""
    import re
    CACHE.mkdir(parents=True, exist_ok=True)
    clean = [re.sub(r"\[[^\]]*\]", "", t).strip() for t in texts]     # v3 감정태그 제거
    cfg = {k: v.get(k) for k in ("voice", "model", "style", "tempo", "rate", "stability", "speed")}
    h = _key({"gemini": "gscript", "edge": "escript", "eleven": "xscript"}[engine], json.dumps(cfg, sort_keys=True, ensure_ascii=False),
             " / ".join(clean))
    outs = [CACHE / f"{h}_{i:03d}.wav" for i in range(len(clean))]
    if not all(o.exists() for o in outs):
        full = CACHE / f"{h}.full.wav"
        if not full.exists():
            {"gemini": _gemini_call, "edge": _edge_call, "eleven": _eleven_call}[engine](" ".join(clean), v, full)
        total = probe(full)["duration"]
        mids = _align_split(full, clean)
        cuts = [0.0] + mids + [total]
        if any(b - a < 0.25 for a, b in zip(cuts, cuts[1:])):
            raise RuntimeError("분할 결과 너무 짧은 문구가 있음")
        tempo = f",atempo={v['tempo']}" if abs(v["tempo"] - 1) > 1e-3 else ""
        for i, o in enumerate(outs):
            ffmpeg("-i", full, "-ss", f"{cuts[i]:.3f}", "-to", f"{cuts[i + 1]:.3f}",
                   "-af", f"afade=t=in:d=0.01,areverse,afade=t=in:d=0.01,areverse{tempo}",
                   "-ar", "48000", "-ac", "1", o)
    return [(o, probe(o)["duration"]) for o in outs]


# ---------------- 문구 단위 ----------------
def _synth_edge(text, v, out):
    _edge_call(text, v, out)


def synth(text, v):
    """문구 하나 → 앞뒤 무음 제거된 48k mono wav. 반환: (경로, 길이초)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cfg = {k: v.get(k) for k in ("provider", "voice", "model", "stability", "similarity", "speed", "tempo", "style")}
    final = CACHE / f"{_key(json.dumps(cfg, sort_keys=True), text)}.wav"
    if not final.exists():
        raw = final.with_suffix(".raw.wav")
        globals()[f"_synth_{v['provider']}"](text, v, raw)
        tempo = f",atempo={v['tempo']}" if abs(v["tempo"] - 1) > 1e-3 else ""
        ffmpeg("-i", raw, "-af", TRIM + tempo, "-ar", "48000", "-ac", "1", final)
        raw.unlink()
    return final, probe(final)["duration"]


# ---------------- 대본 단위 (ElevenLabs with-timestamps) ----------------
def synth_script(texts, v):
    """대본 전체를 1회 생성 → 글자 타임스탬프로 문구 경계를 찾아 자른다.
    문구 사이의 자연스러운 호흡이 그대로 유지되므로 반환 길이에 별도 간격을 더하지 않는다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    full = " ".join(texts)
    cfg = {k: v.get(k) for k in ("voice", "model", "stability", "similarity", "speed", "tempo")}
    h = _key("script", json.dumps(cfg, sort_keys=True), full)
    outs = [CACHE / f"{h}_{i:03d}.wav" for i in range(len(texts))]
    if not all(o.exists() for o in outs):
        r = _xi_post("/with-timestamps", v, {"text": full}).json()
        al = r.get("alignment") or r.get("normalized_alignment")
        mp3 = CACHE / f"{h}.mp3"
        mp3.write_bytes(base64.b64decode(r["audio_base64"]))
        (CACHE / f"{h}.alignment.json").write_text(json.dumps(al, ensure_ascii=False))
        starts, ends, chars = al["character_start_times_seconds"], al["character_end_times_seconds"], al["characters"]
        if "".join(chars) != full:
            raise RuntimeError("타임스탬프 글자열이 대본과 다릅니다(정규화 발생) → mode=line 으로 재시도하세요")
        # 문구별 [첫 글자 시작, 마지막 글자 끝]
        spans, pos = [], 0
        for t in texts:
            a, b = pos, pos + len(t) - 1
            spans.append((starts[a], ends[b]))
            pos += len(t) + 1
        total = probe(mp3)["duration"]
        cuts = [max(0.0, spans[0][0] - 0.03)]
        for (s0, e0), (s1, _) in zip(spans, spans[1:]):
            cuts.append((e0 + s1) / 2)           # 앞 문구 끝과 다음 문구 시작의 중간에서 자름
        cuts.append(min(total, spans[-1][1] + 0.25))
        tempo = f",atempo={v['tempo']}" if abs(v["tempo"] - 1) > 1e-3 else ""
        for i, o in enumerate(outs):
            ffmpeg("-i", mp3, "-ss", f"{cuts[i]:.3f}", "-to", f"{cuts[i + 1]:.3f}",
                   "-af", f"afade=t=in:d=0.01,areverse,afade=t=in:d=0.01,areverse{tempo}",
                   "-ar", "48000", "-ac", "1", o)
    return [(o, probe(o)["duration"]) for o in outs]


def synth_lines(texts, v=None):
    """render.py 진입점. 반환: [(wav, 길이)], 문구 사이 간격을 더해야 하는지 여부."""
    v = voice_cfg(v)
    if v["provider"] == "edge":
        # 기본: 무료 Edge 음성(대본 1회 생성 → Whisper 정렬 분할). 막히면 Gemini 로 대체하고 경고를 남긴다.
        try:
            return synth_script_gemini(texts, v, "edge"), False
        except Exception as e:
            print(f"::warning::Edge 음성 사용 불가 → Gemini 로 대체 ({str(e)[:200]})")
        v = voice_cfg({"provider": "gemini", "voice": EDGE_TO_GEMINI.get(v["voice"], "Puck")})
    if v["provider"] == "gemini":
        # 문구마다 Gemini 를 부르는 모드(편당 20~30회)는 무료 한도를 바로 소진 → 쓰지 않는다.
        # Gemini 가 막히면(한도·오류) 같은 대본을 무료 Edge 음성으로 1회 생성해 분할한다.
        try:
            return synth_script_gemini(texts, v, "gemini"), False
        except Exception as e:
            print(f"::warning::Gemini 실패 → Edge 음성으로 대체(품질 낮음, 검수 시 확인): {str(e)[:200]}")
        try:
            return synth_script_gemini(texts, v, "edge"), False
        except Exception as e:
            print(f"[tts] Edge 대본 분할 실패 → Edge 문구 모드: {str(e)[:200]}")
            v = {**v, "provider": "edge"}
    if v["provider"] == "elevenlabs":
        # 대본 1회 생성 → Whisper 정렬. 채널에 배정된 계정(account)부터 시도하고, 크레딧 소진 등으로 실패하면
        # 다음 계정 → 모두 실패하면 Gemini 로.
        accs = xi_accounts() or [{"key": None, "voice_id": v.get("voice")}]
        start = int(v.get("account", 0)) % len(accs)
        for i in range(len(accs)):
            a = accs[(start + i) % len(accs)]
            try:
                return synth_script_gemini(texts, {**v, "api_key": a["key"], "voice": a["voice_id"]}, "eleven"), False
            except Exception as e:
                print(f"::warning::ElevenLabs 계정 {(start + i) % len(accs) + 1} 실패 → 다음 계정 ({str(e)[:150]})")
        print("::warning::ElevenLabs 모든 계정 실패 → Gemini 로 대체")
        return synth_lines(texts, {"provider": "gemini", "voice": "Puck"})
    return [synth(t, v) for t in texts], True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="+", help="문구 1개 이상 (여러 개면 대본 모드 테스트)")
    ap.add_argument("--provider")
    ap.add_argument("--voice")
    ap.add_argument("--model")
    ap.add_argument("--mode")
    ap.add_argument("--tempo", type=float)
    a = ap.parse_args()
    v = {k: getattr(a, k) for k in ("provider", "voice", "model", "mode", "tempo") if getattr(a, k)}
    res, _ = synth_lines(a.text, v)
    for t, (p, d) in zip(a.text, res):
        print(f"{d:5.2f}s  {len(t.replace(' ', '')) / d:4.1f}자/초  {t}  →  {p}")
