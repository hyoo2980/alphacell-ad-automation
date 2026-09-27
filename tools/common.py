"""공통 유틸: 경로, ffmpeg 실행, 설정 로드."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
LIB = ROOT / "library"
REF_DIR = ROOT / "참고영상 소스"
OUT_DIR = ROOT / "광고 결과 영상"
WORK = ROOT / "work"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()   # 줄 끝 주석 제거
                if v:
                    os.environ.setdefault(k.strip(), v)


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


_PROFILE = None


def set_profile(name):
    """EDL 의 "style_profile" → config/styles/<name>.json 을 기본 style.json 위에 덮어씀."""
    global _PROFILE
    _PROFILE = name


def _merge(base, over):
    for k, v in over.items():
        base[k] = _merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def style():
    st = load_json(CONFIG / "style.json")
    chain, name = [], _PROFILE
    while name:   # "extends": 채널별 프로파일 → 제품군 프로파일 → style.json 순서로 덮어씀
        prof = load_json(CONFIG / "styles" / f"{name}.json")
        chain.append(prof)
        name = prof.get("extends")
    for prof in reversed(chain):
        st = _merge(st, prof)
    return st


def brand(name=None):
    """config/brands/<name>.json (없으면 config/brand.json)."""
    p = CONFIG / "brands" / f"{name}.json" if name else CONFIG / "brand.json"
    return load_json(p)


def parse_rich(text):
    """'이건 {green|단 5일} 만에' → [('이건 ', None), ('단 5일', 'green'), (' 만에', None)]"""
    import re
    out, pos = [], 0
    for m in re.finditer(r"\{([#\w]+)\|([^}]*)\}", text):
        if m.start() > pos:
            out.append((text[pos:m.start()], None))
        out.append((m.group(2), m.group(1)))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], None))
    return out


def plain(text):
    return "".join(t for t, _ in parse_rich(text))


def run(cmd, capture=False):
    """ffmpeg 등 외부 명령 실행. 실패하면 stderr 끝부분을 포함해 예외."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-2000:]}")
    return r.stdout if capture else r


def ffmpeg(*args):
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)], capture=True)
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return {
        "duration": float(d["format"].get("duration", 0) or 0),
        "w": int(v["width"]) if v else None,
        "h": int(v["height"]) if v else None,
        "has_audio": a is not None,
    }


def hex_to_ass(hex_color, alpha=0):
    """#RRGGBB -> &HAABBGGRR (ASS 색상 표기)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def color(name_or_hex):
    c = style()["colors"]
    return c.get(name_or_hex, name_or_hex)


# ---------- 폰트 (Mac/Windows 공용) ----------
FONT_FILES = {   # 패밀리명 → 파일명 후보 (fc-match 가 없는 Windows 대비)
    ("Gmarket Sans TTF", True): ["GmarketSansTTFBold.ttf", "GmarketSansBold.otf"],
    ("Gmarket Sans TTF", False): ["GmarketSansTTFMedium.ttf", "GmarketSansMedium.otf"],
    ("Nanum Myeongjo", False): ["NanumMyeongjo.ttf", "NanumMyeongjo.ttc"],
}


def font_dirs():
    import sys
    home = Path.home()
    dirs = [ROOT / "assets" / "fonts"]
    if sys.platform == "darwin":
        dirs += [home / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    elif sys.platform == "win32":
        dirs += [Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts", Path("C:/Windows/Fonts")]
    else:
        dirs += [home / ".local/share/fonts", home / ".fonts", Path("/usr/share/fonts")]
    return [d for d in dirs if d.exists()]


def font_path(family, bold=True):
    """폰트 파일 경로: assets/fonts → OS 폰트 폴더 → fc-match 순."""
    names = FONT_FILES.get((family, bold)) or FONT_FILES.get((family, not bold)) or []
    for d in font_dirs():
        for n in names:
            for p in d.rglob(n):
                return str(p)
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}", f"{family}:{'bold' if bold else 'regular'}"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except FileNotFoundError:
        pass
    raise FileNotFoundError(f"폰트 '{family}' 를 찾지 못했습니다 → assets/fonts/ 에 {names or family} 파일을 넣으세요")


def has_hangul(path):
    """글꼴에 한글(가~힣) 글리프가 있는지. 없으면 자막이 네모(□)로 나온다."""
    from fontTools.ttLib import TTFont
    cmap = TTFont(path, fontNumber=0, lazy=True).getBestCmap() or {}
    return all(c in cmap for c in (0xAC00, 0xB098, 0xD7A3))


def ass_family(family, bold=True):
    """설정의 패밀리명 → 실제 글꼴 파일의 패밀리명(libass 가 찾는 이름). 한글 없는 글꼴이면 렌더 중단."""
    from PIL import ImageFont
    p = font_path(family, bold)
    if not has_hangul(p):
        raise RuntimeError(f"글꼴에 한글이 없습니다: {p} → 자막이 네모로 나오므로 렌더 중단")
    return ImageFont.truetype(p, 20).getname()[0]


def fonts_dir_for_ass():
    """libass fontsdir: assets/fonts 에 폰트가 있으면 그 폴더, 없으면 Gmarket 이 있는 OS 폴더."""
    return str(Path(font_path("Gmarket Sans TTF", True)).parent)
