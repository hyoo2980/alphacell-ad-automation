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
입력: 채널 id, 개수 N, 오늘 날짜. **할 일은 EDL 파일 작성까지**다. 렌더·업로드·검수·프레임 확인은 하지 않는다
(워크플로가 렌더·업로드를 한 번 실행한다). 사람에게 질문할 수 없으니 판단은 이 문서·가이드 기준으로 한다. 짧게 끝낸다.
1. `cd tools && python assign.py <채널id> <오늘 YYYY-MM-DD>` → **오늘의 배정**(훅 유형·소재 각도·보이스·길이·
   첫 컷 후보·style_profile)을 그대로 따른다. 5채널이 동시에 돌아 서로의 오늘 EDL 을 못 보므로, 채널 간 차이는
   이 배정이 보장한다. `library/PATTERNS_<group>.md`, `config/brands/<brand>.json`, 가이드 9-1(EDL 스키마)·11-A 를
   읽는다. 예시는 `edl/examples/`(구조만 참고, 문구·컷을 베끼지 않는다).
2. 중복 방지(**유사 영상은 노출 제한 대상**): `edl/` 아래 최근 14일 EDL(모든 채널 — 5채널이 같은 제품)의
   훅 문구·헤드라인·첫 컷·컷 순서를 훑고 겹치지 않게 쓴다. 대사는 매번 새로 쓰고(예시·과거 EDL 문장 재사용 금지),
   컷 순서·소스 조합도 과거와 달라야 한다. 문구별 자막 스타일(`box`/`outline` 섞는 위치)과 강조 색도 과거와 다르게.
3. 문구 단위 대본(가이드 11-A): 1문구 = 자막 1개 = 0.8~1.3초 = 공백 제외 14자 이내, 첫 문구에 문제/결과,
   3초 안 컷 전환, 인사·브랜드명 시작 금지, 숫자는 `vo` 에 한국어 읽기. 전체 20~45초.
4. 컷 매칭: `library/index.json` 의 `labels.visual`/`beat`/`desc`. `has_text=true`·`quality 1` 제외,
   같은 컷 반복 피하기, 부정적 문맥에 긍정 비주얼 금지. src 는 index 의 경로 그대로.
5. `edl/<채널id>/<YYMMDD>_<채널id>_v<번호>_<바꾼축-값>.json` 저장(`brand`·`style_profile` 은 배정값,
   `voice`: {"provider": "gemini", "voice": <배정 보이스>}, `disclaimer: true`). 같은 날 파일이 이미 있으면 번호를 올린다.
6. `cd tools && python check.py ../edl/<채널id>/<파일>.json` 한 번 실행 → ERR(소스 없음 등)만 고친다.
   `[표현위험]` 이 나오면 준수 표현으로 바꾼다. 그 외 WARN 때문에 반복 수정하지 않는다.
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
`claims_risky` 에 걸리는 표현은 쓰지 않는다. **할인율·마감·재고 같은 오퍼는 브랜드 설정의 `offer_verified` 가
true 일 때만** 쓴다(가이드의 62% 할인 등은 다른 사람 광고에서 온 값이라 미확인).

## 소스 추가 (로컬, 사람 요청 시)
드라이브에 파일 추가 → `drive_sync.py` → `sources.py scan "../참고영상 소스/<폴더>" --group <제품군>` →
`library/sources/<group>/sheets/*.jpg` 를 보고 새 항목의 `labels` 기입(비주얼 코드는 가이드 7-2) →
`analyze.py index` → 커밋·푸시.

## 유튜브 업로드
- 비공개 + `publishAt`(채널 슬롯의 다음 빈 시각) 예약 공개, 쇼츠, `containsSyntheticMedia: true`.
- 채널당 GCP 프로젝트 쿼터 하루 10,000유닛(업로드 1건 1,600 → 채널당 최대 6건). 쿼터 리셋 KST 16~17시.
- 구글이 프로젝트를 정책 위반 의심으로 표시하면 쿼터 0 → 그 채널은 `enabled: false`.
