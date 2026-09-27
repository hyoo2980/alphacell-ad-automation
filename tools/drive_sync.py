"""구글 드라이브 공개 폴더 → 로컬 동기화 (config/drive.json).

  python tools/drive_sync.py            # 전체 폴더, 없는 파일만 다운로드
  python tools/drive_sync.py --list     # 다운로드 없이 목록만

- API 키 없이 동작: 폴더 목록은 embeddedfolderview 페이지, 파일은 drive.usercontent 직접 다운로드.
- 파일명은 다운로드 응답(Content-Disposition)의 실제 이름을 쓴다(목록 페이지는 긴 이름을 … 로 자름).
- 이미 받은 파일은 <dest>/.drive_index.json(파일 id → 이름)으로 건너뛴다. 서버에서는 폴더째 캐시한다.
"""
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

from common import CONFIG, ROOT, load_json, save_json

UA = {"User-Agent": "Mozilla/5.0"}


def list_folder(fid):
    for k in range(4):   # 드라이브가 가끔 500 을 냄 → 재시도
        try:
            r = requests.get(f"https://drive.google.com/embeddedfolderview?id={fid}", headers=UA, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:
            if k == 3:
                raise
            print(f"  목록 재시도 {k + 1}: {e}")
            time.sleep(10 * (k + 1))
    entries = re.findall(r'href="https://drive\.google\.com/file/d/([\w-]+)/[^"]*".*?flip-entry-title">([^<]*)<',
                         r.text, re.S)
    subs = re.findall(r'href="https://drive\.google\.com/drive/folders/([\w-]+)"', r.text)
    return [(i, html.unescape(t)) for i, t in entries], sorted(set(subs))


def _filename(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="([^"]+)"', cd)
    if not m:
        return fallback
    try:   # requests 는 헤더를 latin-1 로 읽음 → 원래 UTF-8 바이트로 복원
        return m.group(1).encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return m.group(1)


def download(fid, dest_dir, fallback):
    url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    for k in range(4):
        try:
            with requests.get(url, headers=UA, stream=True, timeout=300) as r:
                r.raise_for_status()
                if "text/html" in r.headers.get("Content-Type", ""):
                    raise RuntimeError("다운로드 대신 HTML 응답(공유 권한/할당량 확인)")
                name = _filename(r, fallback).replace("/", "_")
                tmp = dest_dir / (name + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(dest_dir / name)
                return name
        except Exception as e:
            if k == 3:
                raise
            print(f"  재시도 {k + 1}: {e}")
            time.sleep(10 * (k + 1))


def sync(folder, list_only=False):
    dest = ROOT / folder["dest"]
    dest.mkdir(parents=True, exist_ok=True)
    idx_p = dest / ".drive_index.json"
    idx = load_json(idx_p) if idx_p.exists() else {}
    files, subs = list_folder(folder["id"])
    if subs:
        print(f"  ⚠️ 하위 폴더 {len(subs)}개는 받지 않습니다(파일은 폴더 바로 아래에 두세요): {subs}")
    new = [(i, t) for i, t in files if not (i in idx and (dest / idx[i]).exists())]
    print(f"[{folder['dest']}] 드라이브 {len(files)}개, 새로 받을 파일 {len(new)}개")
    if list_only:
        for i, t in files:
            print(f"  {'  ' if (i, t) not in new else '+ '}{t}")
        return
    for n, (i, t) in enumerate(new, 1):
        name = download(i, dest, t)
        idx[i] = name
        save_json(idx_p, idx)
        print(f"  ({n}/{len(new)}) {name}")


if __name__ == "__main__":
    for fo in load_json(CONFIG / "drive.json")["folders"]:
        try:
            sync(fo, "--list" in sys.argv)
        except Exception as e:   # 동기화 실패해도 이미 받아둔(캐시) 파일로 제작은 계속
            print(f"::warning::드라이브 동기화 실패({fo['dest']}) → 기존 파일로 진행: {e}")
