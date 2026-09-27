"""Veo 로 컷 소스 생성 → library/generated/<name>.mp4 + 라벨 json (카탈로그 자동 편입).

인증 (.env):
  GEMINI_API_KEY=...                         # Gemini API 사용 시
  또는 GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT=..., GOOGLE_CLOUD_LOCATION=us-central1
  VEO_MODEL=veo-3.1-generate-preview         # 계정에서 쓰는 모델 ID로 교체

  .venv/bin/python tools/veo.py gum_bleeding_macro \
      --prompt "Extreme macro close-up of inflamed red gums bleeding slightly ..." \
      --beat problem --visual closeup_gum_bad [--image assets/product/box.png] [--aspect 9:16]
"""
import argparse
import os
import time
from pathlib import Path

from common import LIB, ROOT, load_env, save_json

# 자막/헤드라인은 후반에 우리가 올리므로 영상 안 글자·로고는 금지
NO_TEXT = ("No on-screen text, no captions, no subtitles, no watermark, no logos. "
           "Photorealistic, commercial ad footage, sharp focus, natural lighting.")


def generate(name, prompt, beat, visual, image=None, aspect="9:16", seconds=8, negative=None):
    load_env()
    from google import genai
    from google.genai import types

    client = genai.Client()  # GEMINI_API_KEY 또는 Vertex 환경변수 자동 인식
    model = os.environ.get("VEO_MODEL", "veo-3.1-generate-preview")
    kwargs = {}
    if image:
        p = Path(image)
        kwargs["image"] = types.Image(image_bytes=p.read_bytes(),
                                      mime_type="image/png" if p.suffix == ".png" else "image/jpeg")
    op = client.models.generate_videos(
        model=model, prompt=f"{prompt}\n{NO_TEXT}",
        config=types.GenerateVideosConfig(aspect_ratio=aspect, number_of_videos=1,
                                          duration_seconds=seconds,
                                          negative_prompt=negative or "text, letters, subtitles, watermark, cartoon"),
        **kwargs)
    print(f"[veo] {model} 생성 중... ({name})")
    while not op.done:
        time.sleep(10)
        op = client.operations.get(op)
    if op.error:
        raise RuntimeError(op.error)
    vid = op.response.generated_videos[0]
    out = LIB / "generated" / f"{name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    client.files.download(file=vid.video)
    vid.video.save(str(out))
    meta = {"kind": "generated", "clip": str(out.relative_to(ROOT)), "model": model, "prompt": prompt,
            "image": image, "aspect": aspect,
            "labels": {"beat": beat, "visual": visual, "reusable": True, "has_text": False}}
    save_json(out.with_suffix(".json"), meta)
    print(f"[veo] 저장 → {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--beat", required=True)
    ap.add_argument("--visual", required=True)
    ap.add_argument("--image")
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--seconds", type=int, default=8)
    a = ap.parse_args()
    generate(a.name, a.prompt, a.beat, a.visual, a.image, a.aspect, a.seconds)
