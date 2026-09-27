# 메인 업로드 (가이드 6-3). inbox 의 미업로드 영상을 하루 max_per_day 건,
# 비공개 업로드 + publishAt(예약 공개) 로 올리고 done/ 으로 옮긴다.
# 사용법: python upload/scripts/upload_daily.py --channel <id>            (하루치)
#         python upload/scripts/upload_daily.py --channel <id> --all      (있는 것 전부, 쿼터 한도까지)
#         python upload/scripts/upload_daily.py --channel <id> --dry-run  (API 호출 없이 선택·메타·슬롯만 출력)
# 채널별 파일은 upload/channels/<id>/ (token.json, client_secret.json, inbox/, done/, logs/).
# 종료 코드: 0 정상 / 2 로그인 만료 / 3 쿼터 초과 / 4 업로드 실패 있음 → 서버 작업이 실패로 표시돼 메일 알림
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compute_publish_at  # noqa: E402

UPLOAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # upload/
ROOT = UPLOAD   # --channel 이면 upload/channels/<id>/
CFG = {}


def P(key):
    """config 경로(상대면 채널 폴더 기준)."""
    v = CFG[key]
    return v if os.path.isabs(v) else os.path.join(ROOT, v)


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(os.path.join(P("log_dir"), "upload.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(os.path.join(ROOT, "token.json"))
    creds.refresh(Request())
    return creds


def meta_for(video_path):
    side = os.path.splitext(video_path)[0] + ".json"
    m = {}
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            m = json.load(f)
    name = os.path.splitext(os.path.basename(video_path))[0]
    if name[:8].isdigit() and name[8:9] in ("_", "-"):
        name = name[9:]
    desc = m.get("description") or CFG["default_description"]
    tags = m.get("tags") or CFG["default_tags"]
    while sum(len(t) for t in tags) > 500:          # 태그 합계 500자 제한
        tags = tags[:-1]
    return {"title": (m.get("title") or name.replace("_", " "))[:100], "description": desc[:4900],
            "tags": tags, "is_short": m.get("is_short", CFG["is_short"])}


def done_files():
    done = set()
    logp = os.path.join(P("log_dir"), "uploaded.jsonl")
    if os.path.exists(logp):
        with open(logp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    if e.get("video_id"):
                        done.add(e.get("file"))
    return done


def pending_videos():
    done = done_files()
    vids = [f for f in os.listdir(P("video_dir")) if f.lower().endswith((".mp4", ".mov")) and f not in done]
    return sorted(vids, key=lambda f: os.path.getmtime(os.path.join(P("video_dir"), f)))


def record(entry):
    with open(os.path.join(P("log_dir"), "uploaded.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    dry = "--dry-run" in sys.argv
    limit = 999 if "--all" in sys.argv else CFG["max_per_day"]
    logp = os.path.join(P("log_dir"), "uploaded.jsonl")
    reauth = os.path.join(ROOT, "NEEDS_REAUTH.txt")

    if dry:
        todo = pending_videos()[:limit]
        print(f"[dry-run] 대기 {len(pending_videos())}건 중 {len(todo)}건 선택")
        for fname in todo:
            m = meta_for(os.path.join(P("video_dir"), fname))
            print(f"  {fname}\n    제목: {m['title']}\n    태그: {m['tags']}\n    공개 예정: "
                  f"{compute_publish_at.compute(logp, CFG['publish_slots'], min_lead_hours=CFG.get('min_lead_hours', 0))} (실제 업로드 시 슬롯 순차 배정)")
        return

    from google.auth.exceptions import RefreshError
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        creds = load_credentials()
    except (RefreshError, FileNotFoundError) as e:
        with open(reauth, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} 로그인 만료/없음: {e}\n"
                    f"조치: 재로그인 실행 (python upload/scripts/generate_token.py)\n")
        log("로그인 만료 → NEEDS_REAUTH.txt 생성, 업로드 중단")
        return 2
    if os.path.exists(reauth):
        os.remove(reauth)

    yt = build("youtube", "v3", credentials=creds)
    todo = pending_videos()[:limit]
    log(f"대기 {len(pending_videos())}건 중 {len(todo)}건 업로드 시작")
    failed = 0

    for fname in todo:
        path = os.path.join(P("video_dir"), fname)
        m = meta_for(path)
        publish_at = compute_publish_at.compute(logp, CFG["publish_slots"], min_lead_hours=CFG.get("min_lead_hours", 0))
        body = {
            "snippet": {"title": m["title"],
                        "description": m["description"] + ("\n\n#Shorts" if m["is_short"] else ""),
                        "tags": m["tags"], "categoryId": CFG["category_id"]},
            "status": {"privacyStatus": "private",           # publishAt 은 private 에서만 동작
                       "publishAt": publish_at,
                       "selfDeclaredMadeForKids": CFG["made_for_kids"],
                       "containsSyntheticMedia": CFG["contains_synthetic_media"]},
        }
        try:
            resp = yt.videos().insert(part="snippet,status", body=body,
                                      media_body=MediaFileUpload(path, chunksize=-1, resumable=True)).execute()
        except RefreshError:
            with open(reauth, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M} 업로드 중 로그인 만료\n")
            log("업로드 중 로그인 만료 → 중단")
            return 2
        except HttpError as e:
            if "quotaExceeded" in str(e):
                log("쿼터 초과 → 오늘은 여기서 중단(내일 자동 재시도)")
                return 3
            log(f"실패 {fname}: {e}")
            record({"file": fname, "title": m["title"], "video_id": None, "error": str(e)[:200],
                    "at": datetime.now().isoformat(timespec="seconds")})
            failed += 1
            continue
        vid = resp["id"]
        record({"file": fname, "title": m["title"], "video_id": vid, "url": f"https://youtu.be/{vid}",
                "publish_at": publish_at, "at": datetime.now().isoformat(timespec="seconds")})
        log(f"완료 {fname} → https://youtu.be/{vid} ({publish_at} 공개)")
        os.makedirs(P("done_dir"), exist_ok=True)
        shutil.move(path, os.path.join(P("done_dir"), fname))
        side = os.path.splitext(path)[0] + ".json"
        if os.path.exists(side):
            shutil.move(side, os.path.join(P("done_dir"), os.path.basename(side)))
    return 4 if failed else 0


if __name__ == "__main__":
    if "--channel" not in sys.argv:
        sys.exit("--channel <id> 가 필요합니다 (upload/channels.json 참고)")
    ch = sys.argv[sys.argv.index("--channel") + 1]
    with open(os.path.join(UPLOAD, "config.json"), encoding="utf-8") as f:
        CFG.update(json.load(f))
    with open(os.path.join(UPLOAD, "channels.json"), encoding="utf-8") as f:
        CFG.update(json.load(f)[ch])
    ROOT = os.path.join(UPLOAD, "channels", ch)
    os.chdir(ROOT)
    for k in ("video_dir", "done_dir", "log_dir"):
        os.makedirs(P(k), exist_ok=True)
    sys.exit(main() or 0)
