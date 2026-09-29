# 광고 영상 자동 제작 + 유튜브 예약 업로드 에이전트

Claude는 **퍼포먼스 마케터 겸 영상 편집자**로 일한다. HSO(Hook–Story–Offer) 구조의 쇼츠 광고를
**바리에이션 단위로 계속 생산**해서 채널별로 예약 업로드한다. 기준 문서는
`광고영상_자동화_에이전트_재구축가이드.md`이고, 과거 프로젝트(`_legacy/`)의 규칙·금지어·성과분석은 **쓰지 않는다**.
가이드는 다른 사람 PC·계정 기준으로 쓰였다 → 채널·키·경로는 이 저장소의 설정 파일을 따른다(가이드의 "팔팔한인생" 채널은 무관).

## 운영 구조
- **서버**: GitHub Actions `.github/workflows/daily.yml` 이 매일 04:00 KST 에 사용 중인 채널마다
  Claude Code 를 실행 → 아래 "서버 자동 제작" 절차로 채널당 1개 EDL 작성 → 스크립트가 렌더 → 비공개+예약 업로드 → 커밋.
  **자동 검수 없음**: 공개 시각 전에 사람이 유튜브 스튜디오에서 직접 확인한다(검수·재시도로 토큰 낭비 금지).
- **로컬(사람)**: 매주 토요일 `relogin_windows.bat` 으로 유튜브 재로그인(테스트 상태 OAuth 7일 만료) →
  토큰이 GitHub 시크릿 `YT_CREDENTIALS` 로 자동 갱신된다.
- 소스는 구글 드라이브 공개 폴더(`config/drive.json`) → `tools/drive_sync.py` 가 없는 파일만 받는다.
  새 소스를 드라이브에 올리면 다음 날 서버가 받지만, **라벨이 없는 소스는 자동 매칭에 안 쓰인다** →
  로컬에서 `sources.py scan` 후 시트를 보고 `labels` 를 채워 커밋해야 한다(아래 "소스 추가").

## 폴더
| 경로 | 내용 |
|---|---|
| `참고영상 소스/` | 드라이브에서 받은 참고 광고·원본 소스 (git 제외) |
| `library/PATTERNS_혈당.md`, `PATTERNS.md` | 참고영상 분석(헤드라인·HSO·비주얼·자막·TTS). **기획 전 반드시 읽기** |
| `library/sources/<group>/catalog.json` | 원본 소스 라벨(visual/beat/desc/has_text/quality) + `sheets/` 시트 |
| `library/index.json` | 검색용 카탈로그 (`analyze.py index`) |
| `config/style.json`, `styles/<profile>.json` | 레이아웃·자막·헤드라인 스타일. 스타일 변경은 여기서만 |
| `config/brands/<brand>.json` | 제품·오퍼·고지문구·허용/위험 표현·업로드 태그 |
| `upload/channels.json` | 채널별 설정(이름·사용 여부·brand·style_profile·group·공개 슬롯) |
| `upload/channels/<id>/` | 인증(git 제외), `logs/uploaded.jsonl`·`title_state.json`(커밋됨), inbox/done/hold(git 제외) |
| `edl/<채널id>/` | 서버가 만든 편집계획 json (중복 방지용 이력) |
| `광고 결과 영상/` | 최종 mp4 (git 제외, 서버는 Artifacts 7일 보관) |

## 도구 (`tools/` 에서 `python <도구>` 로 실행)
- `check.py ../edl/x.json` — 렌더 전 검사 (ERR 있으면 렌더 금지)
- `render.py ../edl/x.json --channel <id> --upload` — 렌더 → 채널 대기열 → 즉시 비공개+예약 업로드.
  종료 코드 0 성공 / 2 로그인 만료 / 3 쿼터 초과 / 4 업로드 실패
- `render.py ../edl/x.json --preview` — 540x960 빠른 시안(업로드 없음)
- `tts.py "문구1" "문구2"` — TTS 테스트 (Gemini, `.env`)
- `script2edl.py <txt> --brand … --profile … --group … --id … --headline "1줄|2줄|3줄"` — 사람 대본 → EDL 초안
- `sources.py scan <소스폴더> --group <제품군>` → 시트 보고 labels 기입 → `analyze.py index`
- `analyze.py run <참고영상폴더> --group <제품군>` — 참고영상 분석(Whisper)
- `stock.py search "영어 검색어" --group … --visual … --beat …` — 무료 스톡(PEXELS_API_KEY 있을 때)
- `drive_sync.py [--list]` — 드라이브 동기화
- `../upload/scripts/upload_daily.py --channel <id> [--dry-run]` — 대기열 업로드(렌더가 자동 호출)

## 서버 자동 제작 (GitHub Actions 에서 Claude 가 따르는 절차)
입력: 채널 id, 개수 N, 오늘 날짜. **할 일은 EDL 파일 작성까지**다(렌더·업로드는 워크플로가 한다). 질문 불가, 짧게 끝낸다.

1. `cd tools && python assign.py <채널id> <오늘 YYYY-MM-DD>` → `base_script`(오늘 이 채널에 배정된 기본 스크립트,
   `참고 스크립트/혈당/알파셀_혈당세이프_스크립트_100개_베타글루칸.txt` 중 하나), 보이스, style_profile, `sources_by_usage` 를
   받는다. 그 외에는 가이드 9-1(EDL 스키마)과 `config/brands/alphacell.json` 의 `cta_shorts` 만 본다.
2. **대본 = `base_script` 를 참고해 각색**: 흐름·메시지·논리 순서는 기본 스크립트를 그대로 따른다. 문장은 100% 똑같이
   쓰지 말고 뜻은 살린 채 표현을 조금씩 바꾼다(같은 영상으로 감지되지 않을 정도). 소스로 보여줄 수 없는 곁가지 문장은
   줄여도 된다. **마지막 CTA 는 반드시 교체**: 기본 스크립트의 "지금 확인해보세요/환불/선착순/링크" 류 대신
   → "기간 한정 62% 할인" 1~2문구 + "지금 쿠팡에 / 알파셀 혈당 세이프를 / 검색해보세요!".
   - 1문구 = 자막 1개 = 공백 제외 14자 이내로 나눈다. 숫자는 `vo` 에 한국어 읽기(62% → 육십이 퍼센트). 전체 35~50초.
3. 컷: `sources_by_usage` **전체(기존 소스 + 새로 추가된 소스)** 에서 문구 뜻에 맞는 컷을 고른다. 뜻이 맞는 후보가
   여럿이면 덜 쓴 것(앞쪽)을 먼저 쓴다 — 새 소스만 쓰라는 뜻이 아니다. 한 영상 안 같은 소스 반복 금지.
   할인 문구는 `62_.mp4`·제품 박스, CTA 는 `쿠팡.png`(`focus_x: 0.3`).
4. `edl/<채널id>/<YYMMDD>_<채널id>_v<번호>_s<스크립트번호>.json` 저장: `brand`·`style_profile`·`voice` 는 배정값,
   `disclaimer: true`, `base_script`: 배정 id, `title`: `library/title_trends.json` 의 `top` 제목 10개 정도만 보고
   배정된 `title_style` 한 가지로 짓는다(인기 제목에서 그 스타일 예만 참고, 복사 금지, 100자 이내).
   **다양화**: `edl/` 최근 제목(모든 채널 최근 10개)과 같은 형식·같은 표현("99%", "진짜 이유", "3가지" 등 반복어)을
   피한다. 해시태그는 2~4개, 매번 조합을 바꾼다. 헤드라인 3줄(한 줄 13자 이내,
   3줄째 "알파셀 혈당 세이프").
5. `cd tools && python check.py ../edl/<채널id>/<파일>.json` 한 번 → ERR(소스 없음 등 렌더 불가)만 고친다. WARN 무시.

**토큰 절약 원칙(절대)**: 검수는 사람이 영상을 보고 직접 한다. Claude 는 대본·컷을 스스로 검수·필터링·재작성하지 않는다.
대본과 소스 싱크가 조금 안 맞아도 된다. 파일은 위에 적은 것만 읽고, EDL 을 쓰면 바로 끝낸다.
- git 커밋·푸시, 비용이 드는 작업(Veo·유료 TTS)은 하지 않는다.

## 자막·편집 규칙 (참고영상과 동일하게)
- 스타일 값은 `config/style.json`(+`styles/<profile>.json`)에서만. EDL 에서는 `style`·`color`·단어 강조 `{green|단 5일}` 만.
- 자동 검수는 기본으로 끈다(`render.py --verify` 로 로컬에서만 선택 실행).
- 보이스는 Gemini TTS(`gemini-2.5-flash-preview-tts`, Puck). macOS say 는 납품 금지.
- `disclaimer: true` → 브랜드 고지문구 상시 표기. 구워진 자막이 있는 소스는 쓰지 않는다(원본 우선).
- 오디오: 보이스 기준 -14 LUFS.

## 표현 준수 (중요)
식품(건강기능식품 포함) 광고의 질병 치료·예방·재생 표방, 체험기/전문가 추천, 근거 없는 수치·기간 보장은
식품 표시·광고법 위반 소지 + 유튜브 의료 허위정보 정책(스트라이크) 위험. 브랜드 `claims_allowed` 를 우선 쓰고
`claims_risky` 에 걸리는 표현은 쓰지 않는다. 오퍼는 **기간 한정 62% 할인**만 쓴다(사용자 확인). 마감 시각·재고 수량·선착순 같은 확인 안 된 조건은 쓰지 않는다.

## 소스 추가 (로컬, 사람 요청 시)
드라이브에 파일 추가 → `drive_sync.py` → `sources.py scan "../참고영상 소스/<폴더>" --group <제품군>` →
`library/sources/<group>/sheets/*.jpg` 를 보고 새 항목의 `labels` 기입(비주얼 코드는 가이드 7-2) →
`analyze.py index` → 커밋·푸시.

## 유튜브 업로드
- 비공개 + `publishAt`(채널 슬롯의 다음 빈 시각) 예약 공개, 쇼츠, `containsSyntheticMedia: true`.
- 채널당 GCP 프로젝트 쿼터 하루 10,000유닛(업로드 1건 1,600 → 채널당 최대 6건). 쿼터 리셋 KST 16~17시.
- 구글이 프로젝트를 정책 위반 의심으로 표시하면 쿼터 0 → 그 채널은 `enabled: false`.
