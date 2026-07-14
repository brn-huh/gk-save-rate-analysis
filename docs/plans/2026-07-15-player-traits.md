# 계획: 선수 특성(트레잇) 수집 + 드릴다운 표시 (fc-info)

작성: 2026-07-15 · plan-forge (심도: 중대, 국가·클럽 인프라 확장) · 대상: `playerinfo.py`, `db.py`, `config.py`, `export.py`, `render.py`, `cli.py`, tests

## 목표
GK 카드(spid)별 특성(트레잇) 이미지·이름을 fc-info 상세에서 수집·저장하고, 리더보드 카드
드릴다운에 표시한다. 신규특성(금색 배경)은 "신규" 배지로 구분한다.

## 조사로 확정된 사실
- 특성은 **카드(spid)별** — 같은 선수라도 시즌마다 다름(노이어 848167495: 6개 / 818167495: 5개 / 272167495: 4개). → per-spid 수집 필요.
- 검색 API `skill` 은 대부분 None → 상세페이지 필수.
- 특성 HTML: `<img src=".../traits/trait_icon_{code}.png" alt="{name}">` (반복). 정규식으로 (코드,이름) 추출.
- **신규특성 = 아이콘 PNG 배경 금색**(HTML 클래스는 신규/일반 동일 — 구분 표식 없음). 아이콘 색으로만 판별.
- 전체 트레잇 아이콘 분류(1회): 금색(신규)=코드 **50~68 구간**({50,51,52,53,54,55,56,57,59,60,62,63,64,65,66,67,68}), 회색=1~49. 깔끔히 분리.
- 대상 고유 spid = **2,592개** → per-spid 1회씩(약 20~30분, 저속·증분).

## In-scope
- `config.py`: `NEW_TRAIT_CODES` frozenset(위 금색 코드) — 신규 판별 상수.
- `db.py`: `player_trait(spid, ord, trait_code, trait_name, is_new BOOLEAN, PRIMARY KEY(spid,ord))`.
- `playerinfo.py`: `parse_traits(html)->[(code,name)]` + `sync_player_trait(con)` — need spid만 상세 1회, DELETE+INSERT. is_new = code in NEW_TRAIT_CODES. 저속·증분·재시작 안전.
- `cli.py`: `gksave playertrait` (`--delay`, `--limit`).
- `attach_trait(con, cards)` → `c['traits']=[{code,name,is_new}...]`(spid 매칭) + export 연동(리더보드·동일선수).
- `render.py`: 드릴다운(`detailHtml`)에 특성 섹션 — 아이콘(`traits/trait_icon_{code}.png`)+이름, 신규엔 "신규" 배지.
- 파서·수집기·attach·스키마·표시 테스트.

## Out-of-scope (로드맵)
- 특성으로 검색/필터(데이터 구조는 이번에 갖춰 둠), 목록 행에 특성 표시(드릴다운에만), 특성 코드→이름 사전화.

## 수용 기준
1. `gksave playertrait` 실행 시 없는 spid만 상세요청, 완료 후 대부분 카드에 특성 저장(카드당 4~6개).
2. 같은 선수라도 시즌(spid)마다 특성이 다르게 저장됨.
3. 재실행 시 이미 받은 spid는 재요청 안 함(증분), 중단해도 받은 spid 보존.
4. export 후 드릴다운에 특성 아이콘+이름이 보이고, 신규특성엔 "신규" 배지가 붙는다.
5. 특성 없는/파싱실패 카드는 드릴다운 회귀 없이 특성 섹션만 생략.
6. is_new 는 NEW_TRAIT_CODES 로 결정(코드 60=신규, 43=일반).
7. 테스트 통과(파서·attach·스키마·is_new·표시).

## 핵심 결정과 이유
- **스키마: spid 키 `player_trait` (is_new 포함)** — 특성은 카드별·복수라 spid+순서 키. trait_code 로 넥슨 CDN 아이콘 생성, 이름·is_new 부가. `CREATE TABLE IF NOT EXISTS`(추가만).
- **신규 판별은 상수(NEW_TRAIT_CODES)** — 아이콘 배경색 분류는 지금 1회 완료해 코드집합으로 굳힌다. 런타임 이미지 라이브러리 의존 없음(배포·수집 모두). 새 코드 나오면 재분류.
- **수집 상세 HTML 정규식**(헤드리스 불필요). 저속 1회 수동 트리거.
- **대상 전체 gk_sp_id(2592)** — 게이트 낮춰도 대비, 기존 sync 패턴과 동일. 증분이라 재실행 저렴.

## 채택된 가정
- trait_code 는 트레잇 고유 id(코드→이름·배경 안정). 이름은 페이지 파싱(실패 시 이름만 빔).
- 특성 순서 = 페이지 표기 순서. (카드 내 특성 중복 없음)
- 수동 트리거 1회(무인 수집 금지 원칙 유지).

## 남은 리스크
- 파싱 취약: fc-info 가 `traits/trait_icon_` URL 패턴이나 마크업 바꾸면 파서 깨짐 → 테스트 고정, 1회라 재실행 시 인지.
- 신규 코드 확장: 새 게임 업데이트로 금색 코드가 69+ 등장 시 NEW_TRAIT_CODES 갱신 필요(현재는 false negative=배지 누락, 무해).
- 약관: 국가·클럽 때와 동일(1회·소량·저속·참고용·비재배포).

## 작업
1. `config.py` — NEW_TRAIT_CODES frozenset. `db.py` — player_trait 테이블. 검증: 임시 DB 테이블·컬럼 확인.
2. `playerinfo.py` — `_TRAIT` 정규식 + `parse_traits(html)` + `sync_player_trait(con,...)`. 검증: 야신/칸 HTML 스니펫 파싱, is_new(60→True,43→False).
3. `cli.py` — `playertrait` 서브커맨드(`--limit` 소량 시험). 검증: `--limit 3` 3 spid만.
4. `playerinfo.py` — `attach_trait(con, cards)`. `export.py` — 리더보드·동일선수에 부착. 검증: export JSON 에 traits.
5. `render.py` — 드릴다운 특성 섹션(아이콘+이름+신규배지). 검증: 테스트+프리뷰.
6. 테스트: 파서·attach·스키마·is_new·render.
7. 수집: `playertrait --limit 5` 종단검증 → 전체(2592, DB 락 없을 때) → export → 프리뷰 → 커밋 2건(feat, chore).

## 실행 규칙
① 검증 실패 시 다음 작업으로 넘어가지 말고 보고 ② 작업 단위 커밋 ③ 계획 밖 변경 금지(필요 발견 시 보고 후 계획 갱신).
