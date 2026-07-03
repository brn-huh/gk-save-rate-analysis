# gk-save-rate-analysis
FC온라인 공식경기(matchtype=50) 데이터로 골키퍼를 **(선수 × 시즌 × 강화단계 8~13)** 단위로
분석한다. 넥슨 Open API로 매치를 수집해 DuckDB 단일 파일에 쌓고, 선방률·GSAx·존별·타입별
지표를 낸다.

## 무엇을 재나
- **리더보드**: (선수×시즌×강화) 단위 raw 종합선방률. 최소 50경기 게이트 + 표본 경기수 표기.
- **GSAx (난이도 보정)**: 슛 타입·거리로 기대선방을 계산해 `실제선방 − 기대선방`. 쉬운 슛을 많이
  마주한 이점(슛 난이도 교란)을 제거한다. 순위는 GSAx/100슛.
- **강화효과(유저 내)**: 같은 유저가 같은 카드를 서로 다른 강화로 쓴 경우만 비교 → 유저 실력 교란 제거.
- **동일 선수 시즌 비교**: 같은 선수명의 여러 시즌·강화 선방률.
- **카드 상세**: 거리 존별(초근/근/중/원)·슛 타입별(감아차기/낮은슛/헤더/파워샷…) 선방률.

> ⚠️ raw 선방률은 카드 성능이 아니라 그 카드를 쓰는 유저 실력·수비 라인·슛 난이도가 섞인 값이다.
> 리더보드는 카드 추천이 아니다. 슛 난이도는 GSAx, 유저 실력은 강화효과(유저 내)가 각각 보정한다.

## 설치
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export NEXON_API_KEY=발급키
# 선택: 수집 속도 (기본 5, 넥슨 한도 분당1000=지속 안전 ~15)
export GKSAVE_RATE=10
```

## 사용
```bash
# 1) 실측 게이트 (엔드포인트·선방정의·강화범위 확인)
gksave spike

# 2) 수집: 닉네임 시드 → 스노우볼. 재개 가능(끊겨도 이어서). 
gksave collect --seed-nicknames "닉1,닉2" --max-matches 30000
gksave collect --refresh                 # 처리한 유저 다시 열어 새 경기 보충
gksave collect --since 2026-07-01        # 특정 날짜 이후 매치만
# 기본 수집 하한: config.COLLECT_MIN_DATE (2026-03-26) 이전 매치는 자동 제외.
#   시즌/업데이트 경계 이전 데이터는 메타가 달라 분석에서 뺀다. --since 로 덮어쓰기 가능.

# 3) 집계·표출
gksave build                             # raw_match 재파싱 → gk_match/shot (30k ≈ 19초)
gksave meta                              # 선수명·시즌 캐시 (최초 1회)
gksave export --gate 50 --out out        # 리더보드 JSON/CSV + 공개 index.html
gksave export --days 30 --out out        # 최근 30일만

# 4) 조회
gksave leaderboard --gate 50 --top 20    # raw 선방률 순위
gksave gsax --gate 50 --top 20           # GSAx(난이도 보정) 순위
gksave card <spId> --grade 8             # 카드 상세 (거리 존별·타입별)
open out/index.html                      # 정적 페이지 (GitHub Pages에 그대로 올려도 됨)
```

DuckDB 직접 조회(뷰 제공):
```bash
duckdb -readonly data/gksave.duckdb
#   SELECT * FROM card_stats ORDER BY matches DESC LIMIT 10;   -- 카드×강화 요약
#   SELECT * FROM shot_readable WHERE player_name='마누엘 노이어' LIMIT 20;
```

## 파이프라인
```
닉네임 → /v1/id → ouid ─┐
                        ▼ /v1/user/match 스노우볼 (ouid harvest)
                 raw_match(JSON, match_id PK dedup)   ← DuckDB 단일 파일
                        ▼ 재파싱(스트리밍 + CSV COPY)
                 gk_match + shot   ← GK(spPosition==0) ↔ 상대 shootDetail,
                        │             result 1=선방/3=실점, PK/자책골 제외, spGrade 8~13
                        ▼ GROUP BY
                 leaderboard · gsax · grade_effect · same_player → JSON/CSV/HTML
```
> 전역 피드 `/v1/match` 는 쓰지 않는다 — matchId 가 match-detail 로 안 풀림(T0 실측).
> 유효 경로는 `id → user/match → match-detail` 뿐.

## 데이터 저장 (DuckDB 단일 파일 `data/gksave.duckdb`)
| 테이블/뷰 | 역할 |
|---|---|
| `raw_match` | 원본 match-detail JSON (PK dedup) |
| `gk_match` · `shot` | 파싱 결과 (재생성 가능) |
| `meta_spid` · `meta_season` | 선수명·시즌 캐시 |
| `frontier` | 스노우볼 ouid 큐 (영속·재개) |
| `shot_readable` · `card_stats` | 이름·시즌 붙인 조회용 뷰 |

## 테스트
```bash
pytest -q          # 31 tests
```
