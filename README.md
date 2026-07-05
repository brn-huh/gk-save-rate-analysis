# gk-save-rate-analysis

FC온라인 공식경기(matchtype=50) 데이터로 **골키퍼를 (선수 × 시즌 × 강화단계 8~13) 단위로**
분석한다. 넥슨 Open API로 매치를 수집해 DuckDB 단일 파일에 쌓고, 선방률·GSAx·거리존별·
타입별 지표를 내서 공개용 정적 웹페이지로 뽑는다.

> ⚠️ raw 선방률은 카드 성능이 아니라 그 카드를 쓰는 **유저 실력·수비 라인·슛 난이도가 섞인 값**이다.
> 리더보드는 카드 추천이 아니다. 슛 난이도는 **GSAx**, 유저 실력은 **강화효과(유저 내)** 가 각각 보정한다.

---

## ✨ 특징

- **교란을 두 축으로 보정** — raw 선방률의 두 교란을 각각 제거한다.
  - 슛 난이도 → **GSAx**(기대선방 모델), 유저 실력 → **강화효과(within-ouid)**
- **강화단계를 퉁치지 않음** — (선수 × 시즌 × 강화단계) 단위로 각각 순위.
- **표본 투명성** — 최소 50경기 게이트 + 모든 순위에 표본 경기수 표기.
- **드릴다운** — 카드별 거리 구간별(초근/근/중/원)·슛 타입별(감아차기/헤더/파워샷…) 선방률.
- **인터랙티브 공개 페이지** — 검색·정렬·행 클릭 펼침이 되는 자기완결 HTML(외부 의존 0).
- **복원력 있는 수집** — 스노우볼 BFS + 429 백오프 + 중단 후 재개 + 날짜 컷오프/갱신 모드.
- **빠른 재빌드** — 스트리밍 파싱 + DuckDB COPY 로 4만 매치 ≈ 28초.

---

## 📊 지표 설명

| 지표 | 정의 | 무엇에 답하나 |
|---|---|---|
| **종합선방률** | 선방(result=1) / (선방 + 실점). PK·자책골 제외 | "얼마나 막나" (단, 유저 교란 포함) |
| **GSAx** | 실제선방 − 기대선방. 기대선방은 (슛 타입 × 거리 구간) 리그 평균 선방률로 계산 | "슛 난이도 감안하면 얼마나 잘 막나" |
| **GSAx(초근제외)** | 위 GSAx에서 초근거리(<5m) 뽀록성 슛 제외 | "막을 만한 슛에서의 순수 실력" |
| **강화효과(유저 내)** | 같은 유저·같은 카드에서 강화 1단계 상승당 평균 선방률 변화 | "강화하면 진짜 더 막나" (유저 실력 상쇄) |
| **거리 존별** | 초근(0-5m)/근(5-11m)/중(11-16.5m)/원(16.5m+) 구간별 선방률 | "가까운/먼 슛 중 뭘 잘 막나" |
| **타입별** | 감아차기·낮은슛·헤더·파워샷 등 슛 타입별 선방률 | "어떤 슛에 강/약한가" |

> GSAx 순위는 볼륨 왜곡을 피하려고 **GSAx/100슛**(슛당 초과선방 × 100)으로 매긴다. 리그 전체 Σ GSAx = 0.

---

## 🖥 공개 페이지 (`out/index.html`)

`gksave export` 가 만드는 자기완결 HTML. 그대로 열거나 GitHub Pages에 올리면 URL로 공유된다.
- **검색** — 선수 이름 필터
- **정렬 토글** — 선방률 / GSAx / GSAx(초근제외) / 표본
- **행 클릭 → 펼침** — 그 카드의 거리 구간별(막대)·슛 타입별 표
- **강화효과 요약 + 동일선수 시즌 비교**(아코디언)
- 상단에 **데이터 수집 기간**, 하단에 **NEXON Open API 출처** 명시

---

## 설치

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# API 키·설정은 .env.local 에 (커밋 안 됨). export 로 덮어쓰기도 가능.
cp .env.local.example .env.local
#   .env.local 을 열어 NEXON_API_KEY 를 발급키로 채운다
#   (GKSAVE_RATE / GKSAVE_CONCURRENCY / GKSAVE_DATA_DIR 도 여기서 조정)
```

> 🔒 **보안**: `.env.local`·`data/`(ouid 포함)·`out/` 은 `.gitignore` 로 커밋되지 않는다.
> API 키는 **수집 시에만** 쓰이고 공개 배포물(`out/index.html`, JSON)에는 키·ouid 등
> 민감정보가 전혀 없다 → Vercel 등에 정적 배포해도 안전.

## 사용법

```bash
# 1) 실측 게이트 — 엔드포인트·선방정의·강화범위·시즌디코드 확인
gksave spike

# 2) 수집 — 닉네임 시드 → 스노우볼 BFS. 재개 가능(끊겨도 이어서).
gksave collect --seed-nicknames "닉1,닉2" --max-matches 30000
gksave collect --refresh              # 처리한 유저 다시 열어 새 경기 보충
gksave collect --since 2026-07-01     # 특정 날짜 이후만 (미지정 시 기본 하한 2026-03-26 적용)

# 3) 집계·표출
gksave build                          # raw_match 재파싱 → gk_match/shot (4만 ≈ 28초)
gksave meta                           # 선수명·시즌 캐시 (최초 1회)
gksave export --gate 50 --out out     # 리더보드 JSON/CSV + 공개 index.html
gksave export --days 30 --out out     # 최근 30일만

# 4) 조회 (콘솔)
gksave leaderboard --gate 50 --top 20             # raw 선방률 순위
gksave gsax --gate 50 --top 20                    # GSAx 순위
gksave gsax --gate 50 --exclude-shortest          # GSAx(초근 제외)
gksave card <spId> --grade 8                      # 카드 상세 (거리존별·타입별)
open out/index.html                               # 공개 페이지 열기
```

DuckDB 직접 조회 — 이름·시즌 붙인 뷰 제공:
```bash
duckdb -readonly data/gksave.duckdb
#   SELECT * FROM card_stats ORDER BY matches DESC LIMIT 10;      -- 카드×강화 요약
#   SELECT * FROM shot_readable WHERE player_name='마누엘 노이어' LIMIT 20;
```

---

## 파이프라인

```
닉네임 → /v1/id → ouid ─┐
                        ▼ /v1/user/match 스노우볼 (응답에서 ouid harvest → 큐에 추가)
                 raw_match (JSON, match_id PK dedup)      ← DuckDB 단일 파일
                        ▼ 재파싱 (스트리밍 + CSV COPY)
                 gk_match + shot   ← 우리 GK(spPosition==0) ↔ 상대 shootDetail
                        │             result 1=선방 / 3=실점, PK·자책골 제외, spGrade 8~13
                        ▼ GROUP BY
                 leaderboard · gsax · grade_effect · same_player · zones/types
                        ▼
                 out/leaderboard.{json,csv} + index.html
```

> **전역 피드 `/v1/match` 는 쓰지 않는다** — 그 matchId 가 match-detail 로 안 풀린다(400, T0 실측).
> 유효 경로는 `id → user/match → match-detail` 뿐이라 시드를 ouid 기반으로 잡는다.

## 데이터 저장 (DuckDB 단일 파일 `data/gksave.duckdb`)

| 테이블/뷰 | 역할 |
|---|---|
| `raw_match` | 원본 match-detail JSON (match_id PK로 dedup) |
| `gk_match` · `shot` | 파싱 결과 (raw에서 재생성 가능, 날짜 포함) |
| `meta_spid` · `meta_season` | 선수명·시즌 캐시 (정적 파일) |
| `frontier` | 스노우볼 ouid 큐 (영속 → 재개) |
| `shot_readable` · `card_stats` | 이름·시즌 붙인 조회용 뷰 |

## 넥슨 Open API 참고

- 인증: 헤더 `x-nxopen-api-key`
- 쓰는 엔드포인트: `/v1/id`(닉→ouid) · `/v1/user/match`(유저 매치) · `/v1/match-detail`(상세) ·
  `/static/fconline/meta/{spid,seasonid}.json`(메타)
- 호출 한도: **초당 50 / 분당 1,000(병목) / 일 2,000만** → 지속 안전 레이트 ~15/s

## 테스트

```bash
pytest -q          # 32 tests
```
