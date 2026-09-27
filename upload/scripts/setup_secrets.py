# 서버(GitHub Actions)가 쓸 비밀값을 GitHub 저장소 시크릿에 등록한다. setup_secrets.bat 더블클릭으로 실행.
#   1) GEMINI_API_KEY          — 음성(TTS)용. aistudio.google.com/apikey 에서 복사한 키를 붙여넣기
#   2) CLAUDE_CODE_OAUTH_TOKEN — 서버의 Claude 가 내 Claude 구독으로 대본을 쓰게 하는 1년짜리 토큰
#   3) YT_CREDENTIALS          — 유튜브 채널 로그인 토큰(지금 PC 에 있는 것)
# 값은 화면에 표시되지 않고 GitHub 에 암호화되어 저장된다(공개 저장소여도 남이 볼 수 없음).
import getpass
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relogin  # noqa: E402

ROOT = relogin.ROOT


def gh_set(name, value):
    r = subprocess.run(["gh", "secret", "set", name], input=value.strip(), text=True, cwd=ROOT)
    print(f"  → {name}: {'등록 완료' if r.returncode == 0 else '실패'}")
    return r.returncode == 0


def main():
    print("=== 1/3 Gemini API 키 ===")
    print("aistudio.google.com/apikey 에서 '키 복사' 후 여기에 붙여넣고 Enter (화면엔 안 보입니다. 건너뛰려면 그냥 Enter)")
    k = getpass.getpass("Gemini 키: ")
    if k.strip():
        gh_set("GEMINI_API_KEY", k)

    print("\n=== 2/3 Claude 토큰 ===")
    if input("Claude 토큰을 새로 발급할까요? (y/n): ").strip().lower() == "y":
        print("브라우저가 열리면 Claude 계정으로 승인하세요. 끝나면 이 창에 'sk-ant-oat...' 로 시작하는 토큰이 출력됩니다.")
        subprocess.run("claude setup-token", shell=True)
        t = getpass.getpass("\n위에 출력된 토큰(sk-ant-oat...)을 복사해서 붙여넣고 Enter: ")
        if t.strip():
            gh_set("CLAUDE_CODE_OAUTH_TOKEN", t)

    print("\n=== 3/3 유튜브 토큰 올리기 ===")
    relogin.push(relogin.channels())
    input("\n끝났습니다. Enter 로 닫기")


if __name__ == "__main__":
    main()
