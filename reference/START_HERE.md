# START HERE — keeper 프로젝트 재시작 안내

이 `reference/` 폴더를 새 프로젝트 폴더로 통째로 복사한 뒤, 새 세션에서 아래 파일들을 읽게 하고 시작하면 됩니다.

## 먼저 읽을 것 (우선순위 순)
1. **DESIGN.md** — 설계 문서. 문제정의 / API 데이터사전 / 수집 파이프라인 / 슛 단위 테이블 / 분석 Level 0~3 / 한계 / 다음 단계. **이거 하나가 전체 기준선.**
2. **api-3-match.yaml** — ⭐ 가장 중요. `match-detail` 전체 응답 구조 (선수별 spId·spGrade·status, 팀 shoot, shootDetail). GK 선방률 산출의 유일한 원천.
3. **api-5-metadata.yaml** — 코드 매핑 (matchtype에서 공식경기=50 확정, spposition에서 GK=0 확정, spid→선수명).
4. **api-2-user.yaml** / **api-4-ranker.yaml** / **api-6-image.yaml** — 나머지 카테고리 스펙 (참고용).

## 새 세션에 줄 한 줄 프롬프트 (예시)
> "reference/DESIGN.md와 reference/api-*.yaml을 먼저 읽어줘. FC온라인 Open API로
> 공식경기(matchtype=50) 매치 데이터를 수집해서 골키퍼 카드의 강화단계별 선방률을
> 교란보정(GSAx) 분석하는 프로젝트야. DESIGN.md의 '수집 파이프라인 경로 A'와
> '분석 Level 1(GSAx)'부터 구현 계획을 세워줘."

## 반드시 기억할 2가지 (재플랜 시 빠뜨리면 안 됨)
1. 랭커 API로는 ouid를 못 얻는다 → `/v1/match`로 매치ID를 직접 수집(경로 A).
2. 선수 status에 선방 필드가 없다 → 선방률은 같은 경기 **상대팀 shootDetail 교차 join**으로만 나온다.

## 먼저 확정할 것 (DESIGN.md Open Questions)
- `meta/matchtype.json` 실값으로 공식경기=50 확인
- `/v1/match` 최대 offset / 데이터 보존 범위
- 발급 API 키의 레이트리밋(429) 수치

## 원본 스펙 출처 (다시 받고 싶을 때)
- base: `https://openapi.nexon.com/static/api/fconline/`
- match: `3_ko_script20260521005954.yaml` (나머지: `{2,4,5,6}_script00000000000000.yaml`)
- 문서 페이지: `https://openapi.nexon.com/ko/game/fconline/?id=2`~`6`
