# gk-save-rate-analysis
골키퍼 선방률 분석 — FC온라인 공식경기(matchtype=50) 데이터로 골키퍼 카드(시즌×선수)와
강화단계(8~13)별 종합선방률을 낸다.

## 무엇을 재나
- **글로벌 리더보드**: 카드 단위 raw 종합선방률 순위. 최소 50경기 게이트 + 표본 경기수 표기.
- **강화효과(유저 내)**: 같은 유저가 같은 카드를 서로 다른 강화단계로 쓴 경우만 비교해
  "강화하면 더 막나"에서 유저 실력 교란을 제거한다.

> ⚠️ raw 선방률은 카드 성능이 아니라 그 카드를 쓰는 유저 실력·수비 라인·슛 난이도가 섞인 값이다.
> 리더보드는 카드 추천이 아니다. 강화 자체의 효과는 유저 내 비교(grade_effect)를 볼 것.

## 설치
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export NEXON_API_KEY=발급키
```

## 사용
```bash
gksave spike                          # T0: 실제 API로 엔드포인트·선방정의·강화범위 실측 (선행 게이트)
gksave collect --seed-pages 5 --max-matches 5000   # 시드 + 스노우볼 수집 (재개 가능)
gksave build                          # raw_match 재파싱 → gk_match/shot 재생성
gksave export --gate 50 --out out     # 리더보드 JSON/CSV
gksave leaderboard --gate 50 --top 20 # 콘솔 출력
```

## 파이프라인
```
/v1/match(50) 시드 ─┐
                    ▼ ouid harvest → /v1/user/match 스노우볼
             raw_match(JSON, PK dedup)  ← DuckDB 단일 파일
                    ▼ 재파싱
             gk_match + shot            ← GK(spPosition==0) ↔ 상대 shootDetail
                    ▼ GROUP BY          ← result 1=선방/3=실점, PK/자책골 제외
             card_leaderboard + grade_effect → JSON/CSV
```

## 테스트
```bash
pytest -q
```
