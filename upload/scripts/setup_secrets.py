# 서버(GitHub Actions)가 쓸 비밀값을 GitHub 저장소 시크릿에 등록한다. setup_secrets.bat 더블클릭으로 실행.
#   1) GEMINI_API_KEY          — 음성(TTS)용. aistudio.google.com/apikey 에서 복사한 키를 붙여넣기
#   2) CLAUDE_CODE_OAUTH_TOKEN — 서버의 Claude 가 내 Claude 구독으로 대본을 쓰게 하는 1년짜리 토큰
#   3) YT_CREDENTIALS          — 유튜브 채널 로그인 토큰(지금 PC 에 있는 것)
# 붙여넣기: 창에서 마우스 오른쪽 클릭 또는 Ctrl+V. 등록 전에 키가 실제로 동작하는지 검사한다.
import json
import os
import re
import subprocess
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relogin  # noqa: E402

ROOT = relogin.ROOT
TTS_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts"


def clean(s):
    return re.sub(r"[\x00-\x1f\s]", "", s or "")   # 붙여넣기 때 섞이는 제어문자·공백·줄바꿈 제거


def gh_set(name, value):
    r = subprocess.run(["gh", "secret", "set", name], input=value, text=True, cwd=ROOT)
    print(f"  → {name}: {'GitHub 등록 완료' if r.returncode == 0 else '등록 실패'}")
    return r.returncode == 0


def gemini_ok(key):
    r = requests.get(TTS_URL, headers={"x-goog-api-key": key}, timeout=30)
    if r.ok:
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def ask_gemini():
    print("=== 1/3 Gemini API 키 ===")
    print("aistudio.google.com/apikey → 키 옆 복사 버튼 → 여기에 붙여넣고 Enter (건너뛰려면 그냥 Enter)")
    for _ in range(3):
        k = clean(input("Gemini 키: "))
        if not k:
            return
        print(f"  입력된 길이 {len(k)}자, 앞부분 {k[:4]}… 확인 중")
        ok, why = gemini_ok(k)
        if ok:
            print("  ✓ 키 정상 동작")
            gh_set("GEMINI_API_KEY", k)
            return
        print(f"  ✗ 이 키로는 Gemini 가 거절합니다 ({why})")
        print("    → AI Studio 에서 '키 만들기'로 새 키를 만들어 다시 붙여넣어 보세요.")


def ask_claude():
    print("\n=== 2/3 Claude 토큰 ===")
    if input("Claude 토큰을 등록할까요? (y/n): ").strip().lower() != "y":
        return
    if input("  토큰을 새로 발급할까요? 이미 복사해 둔 토큰이 있으면 n (y/n): ").strip().lower() == "y":
        print("  브라우저가 열리면 Claude 계정으로 승인하세요. 끝나면 'sk-ant-oat…' 로 시작하는 토큰이 출력됩니다.")
        subprocess.run("claude setup-token", shell=True)
    for _ in range(3):
        t = clean(input("\n토큰(sk-ant-oat…)을 붙여넣고 Enter: "))
        if not t:
            return
        if t.startswith("sk-ant-") and len(t) > 40:
            print(f"  ✓ 형식 정상 ({len(t)}자)")
            gh_set("CLAUDE_CODE_OAUTH_TOKEN", t)
            return
        print(f"  ✗ 형식이 다릅니다(길이 {len(t)}자, 앞부분 {t[:7]!r}). 'sk-ant-oat' 로 시작하는 전체를 복사하세요.")


def _xi_voice(k, name):
    """계정 k 에서 목소리 name 의 voice_id. 내 목소리에 없으면 라이브러리에서 찾아 추가."""
    h = {"xi-api-key": k}
    r = requests.get("https://api.elevenlabs.io/v1/voices", headers=h, timeout=30)
    if not r.ok:
        return None, f"키 거절 (HTTP {r.status_code})"
    mine = [v for v in r.json().get("voices", []) if name.lower() in v["name"].lower()]
    if mine:
        return mine[0]["voice_id"], mine[0]["name"]
    s = requests.get("https://api.elevenlabs.io/v1/shared-voices", headers=h,
                     params={"search": name, "page_size": 10}, timeout=30).json().get("voices", [])
    if not s:
        return None, f"'{name}' 목소리를 찾지 못함 (그 계정의 My Voices 에 추가 후 다시)"
    sv = s[0]
    a = requests.post(f"https://api.elevenlabs.io/v1/voices/add/{sv['public_owner_id']}/{sv['voice_id']}",
                      headers=h, json={"new_name": sv["name"]}, timeout=30)
    if not a.ok:
        return None, f"라이브러리 목소리 추가 실패 (HTTP {a.status_code}: {a.text[:120]})"
    return a.json().get("voice_id", sv["voice_id"]), sv["name"]


def ask_eleven():
    print("\n=== ElevenLabs 계정들 (목소리) ===")
    print("계정 키를 하나씩 붙여넣고 Enter. 채널 1개당 계정 1개(무료 1만 크레딧 ≈ 하루 1편 한 달). 다 넣었으면 빈 Enter.")
    name = input("쓸 목소리 이름 (Enter = taehyung): ").strip() or "taehyung"
    accs = []
    while True:
        k = clean(input(f"  계정 {len(accs) + 1} 키 (끝내려면 Enter): "))
        if not k:
            break
        vid, info = _xi_voice(k, name)
        if not vid:
            print(f"    ✗ {info} → 이 계정은 건너뜀")
            continue
        q = requests.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": k}, timeout=30)
        left = ""
        if q.ok:
            j = q.json()
            left = f", 남은 크레딧 {j.get('character_limit', 0) - j.get('character_count', 0):,}"
        print(f"    ✓ 목소리 {info} ({vid}){left}")
        accs.append({"key": k, "voice_id": vid})
    if accs:
        gh_set("ELEVENLABS_ACCOUNTS", json.dumps(accs))
        print(f"  → 계정 {len(accs)}개 등록. 채널 5개면 5개 이상 권장(6개면 여유).")


def main():
    ask_eleven()
    ask_gemini()
    ask_claude()
    print("\n=== 3/3 유튜브 토큰 올리기 ===")
    relogin.push(relogin.channels())
    input("\n끝났습니다. Enter 로 닫기")


if __name__ == "__main__":
    main()
