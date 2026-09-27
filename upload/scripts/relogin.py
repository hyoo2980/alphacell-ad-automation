# 유튜브 재로그인 + 서버(GitHub) 시크릿 갱신. 테스트 상태 OAuth 는 7일마다 만료 → 매주 토요일 실행.
# 사용법: python upload/scripts/relogin.py                 (사용 중인 채널 전부, 순서대로 브라우저 로그인)
#         python upload/scripts/relogin.py channel1_hyeoldang   (특정 채널만)
#         python upload/scripts/relogin.py --push-only     (로그인 없이 현재 토큰만 GitHub 에 올리기)
# 계정에 유튜브 채널이 여러 개면: 로그인 전에 youtube.com 에서 그 채널로 전환해 둘 것.
# 로그인된 채널 이름이 channels.json 의 name 과 다르면 저장하지 않고 다시 시도한다.
import json
import os
import subprocess
import sys
import webbrowser

UPLOAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(UPLOAD)
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CHROME = {"win32": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          "darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}.get(sys.platform)


def channels():
    with open(os.path.join(UPLOAD, "channels.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def login(ch, cfg):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    d = os.path.join(UPLOAD, "channels", ch)
    cs = os.path.join(d, "client_secret.json")
    if not os.path.exists(cs):
        print(f"  {cs} 없음 → 구글 클라우드 콘솔에서 OAuth 데스크톱 클라이언트 JSON 을 받아 이 이름으로 저장")
        return False
    browser = None
    if CHROME and os.path.exists(CHROME):
        webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(CHROME))
        browser = "chrome"
    for attempt in range(3):
        input(f"\n[{ch}] youtube.com 에서 '{cfg.get('name', ch)}' 채널로 전환했으면 Enter (브라우저가 열립니다)...")
        flow = InstalledAppFlow.from_client_secrets_file(cs, SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, browser=browser) if browser else flow.run_local_server(port=0)
        items = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute().get("items", [])
        got = items[0]["snippet"]["title"] if items else "(채널 없음)"
        print(f"  *** 연결된 채널: {got} ***")
        if cfg.get("name") and got != cfg["name"]:
            print(f"  ⚠️ '{cfg['name']}' 이(가) 아닙니다. 채널을 전환하고 다시 로그인하세요. (저장 안 함)")
            continue
        with open(os.path.join(d, "token.json"), "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        rf = os.path.join(d, "NEEDS_REAUTH.txt")
        if os.path.exists(rf):
            os.remove(rf)
        return True
    return False


def push(chs):
    """사용 중인 채널의 client_secret + token 을 GitHub 시크릿 YT_CREDENTIALS 하나로 올린다."""
    bundle = {}
    for ch, cfg in chs.items():
        if not cfg.get("enabled"):
            continue
        d = os.path.join(UPLOAD, "channels", ch)
        try:
            with open(os.path.join(d, "client_secret.json"), encoding="utf-8") as f:
                cs = json.load(f)
            with open(os.path.join(d, "token.json"), encoding="utf-8") as f:
                tk = json.load(f)
        except FileNotFoundError as e:
            print(f"  {ch}: 파일 없음 {e.filename} → 건너뜀")
            continue
        bundle[ch] = {"client_secret": cs, "token": tk}
    r = subprocess.run(["gh", "secret", "set", "YT_CREDENTIALS"], input=json.dumps(bundle), text=True, cwd=ROOT)
    print(f"\nGitHub 시크릿 YT_CREDENTIALS 갱신: {'성공' if r.returncode == 0 else '실패'} ({', '.join(bundle)})")
    return r.returncode == 0


def main():
    chs = channels()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--push-only" not in sys.argv:
        targets = args or [k for k, v in chs.items() if v.get("enabled")]
        failed = [ch for ch in targets if not login(ch, chs[ch])]
        if failed:
            print(f"\n로그인 실패/건너뜀: {failed}")
    ok = push(chs)
    input("\n끝났습니다. Enter 로 닫기")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
