"""서버(GitHub Actions)용: 시크릿 YT_CREDENTIALS(JSON) → upload/channels/<id>/client_secret.json, token.json 복원.

  YT_CREDENTIALS='{"channel1_hyeoldang": {"client_secret": {...}, "token": {...}}, ...}' python tools/ci_credentials.py
  python tools/ci_credentials.py --enabled-channels   # 사용 중인 채널 목록을 JSON 배열로 출력(워크플로 matrix 용)
"""
import json
import os
import sys

from common import ROOT, load_json

if "--enabled-channels" in sys.argv:
    chs = load_json(ROOT / "upload" / "channels.json")
    only = os.environ.get("ONLY_CHANNEL", "").strip()
    ids = [k for k, v in chs.items() if not k.startswith("_") and v.get("enabled") and (not only or k == only)]
    print(json.dumps(ids))
    sys.exit(0)

raw = os.environ.get("YT_CREDENTIALS")
if not raw:
    sys.exit("YT_CREDENTIALS 시크릿이 없습니다 → 로컬에서 relogin_windows.bat 실행")
for ch, v in json.loads(raw).items():
    d = ROOT / "upload" / "channels" / ch
    d.mkdir(parents=True, exist_ok=True)
    (d / "client_secret.json").write_text(json.dumps(v["client_secret"]), encoding="utf-8")
    (d / "token.json").write_text(json.dumps(v["token"]), encoding="utf-8")
    print(f"인증 복원: {ch}")
