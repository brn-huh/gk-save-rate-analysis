# 계획: 리더보드 개선 4건 (무게·초기화·수집통합·의존성)

작성: 2026-07-15 · plan-forge (심도: 중대 — 산출물 포맷 변경 #1, 수집 리팩터 #7) · 대상: `export.py`, `render.py`, `playerinfo.py`, `cli.py`, `pyproject.toml`, `scripts/`, tests

## 목표
① 초기 페이지 전송 무게를 줄이고(상세 지연 로드) ② 필터 초기화 아이콘 추가 ③ fc-info
수집기를 per-spid 단일 패스로 통합 ④ 1회성 도구(Pillow) 의존성을 명시한다.

## 조사로 확정된 사실
- `export.export()`가 `leaderboard.json`(전체 payload, indent=2, ~5.2MB) + `leaderboard.csv` + `index.html`(payload 임베드) 생성. index.html gzip 0.90MB.
- 리더보드 JSON의 **70%가 드릴다운 전용**(zones/types/extras). slim(상세 제거)만 gzip 0.22MB, 부동소수 4자리 반올림까지 하면 더 작음.
- `detailHtml(c)`가 `c.zones/c.types/c.extras`를 씀(행 클릭 `toggle`에서 호출).
- 수집기: `sync_player_bio`(pid별 881회), `sync_player_trait`(spid별 2592회) 둘 다 `/player/{spid}?grade=1` 요청. 통합 시 per-spid 2592회로 국가·클럽·특성 전부 획득.
- `pyproject`: deps=[httpx,duckdb], optional `dev=[pytest]`. Pillow 없음.

## In-scope
### #1 상세 지연 로드 (A안, 사용자 확정)
- `export`가 index.html엔 **slim 리더보드**(zones/types/extras 제거) 임베드 + 별도 `out/details.json`(`{spid: {zones,types,extras}}`) 생성. 부동소수 4자리 반올림(표시엔 무영향, 무료 이득).
- `render.py`: 첫 드릴다운 때 `fetch('details.json')` 1회(캐시) 후 상세 렌더. 로드 전 "불러오는 중…" 표기. 로드 후 클릭은 즉시. 로드 실패 시 안내.
- **로컬 확인**: `cd out && python3 -m http.server 8000` → `http://localhost:8000`. file:// 직접 열기는 드릴다운 미동작(문서화). 배포(Vercel https)는 정상.

### #5 필터 초기화 아이콘
- 컨트롤 끝에 아이콘 버튼(`↺`, 텍스트 없음, `title/aria-label="필터 초기화"`). 클릭 시 이름·국가/클럽·급여·강화·게이트(200)·정렬(선방률 내림) 전부 리셋 + 입력/활성표시 초기화.

### #7 수집기 통합
- `sync_player_detail(con)` 신설: 우리 gk_sp_id 중 특성 미보유 spid만 per-spid 상세 1회 → 특성(spid 저장) + 국가·클럽(그 pid 미보유면 채움) 동시 upsert. 저속·증분.
- `sync_player_bio`·`sync_player_trait` 제거(대체). CLI `playerbio`·`playertrait` → `playerdetail`. `parse_bio/parse_traits/attach_bio/attach_trait`는 유지(export가 씀).
- **재수집 불필요**(현재 데이터 이미 완비 → no-op). 향후 신규 카드에 한 패스.

### #8 Pillow 의존성 명시
- `scripts/classify_new_traits.py` 커밋: 트레잇 아이콘 배경색 분류로 `NEW_TRAIT_CODES` 재생성(향후 신규 특성 코드 갱신용). `pyproject`에 optional 그룹 `tools=[Pillow]` 추가(런타임 아닌 1회성 도구).

## Out-of-scope
- #6 자동완성(부분일치로 이미 "유벤→유벤투스" 됨), 기본 정렬 GSAx 전환(#2), 툴팁 모바일/키보드 접근성(#3), 비교탭 강화(#4) — 별도 판단.

## 수용 기준
1. index.html에 zones/types/extras 임베드 없음, `out/details.json` 생성됨, 초기 전송(gzip) 현저히 감소.
2. `http://localhost` 로 열고 행 클릭 시 상세(거리존·타입·스탯)가 정상 표시(첫 클릭에 fetch, 이후 즉시).
3. details.json fetch 실패 시 드릴다운이 크래시 없이 안내 문구.
4. 필터 초기화 아이콘 클릭 시 모든 필터·정렬·게이트가 기본으로 돌아가고 목록이 갱신됨.
5. `gksave playerdetail --limit N` 실행 시 특성 미보유 N개 spid만 상세요청, 특성+국가·클럽이 함께 저장됨.
6. `pyproject`에 `tools` 그룹(Pillow), `scripts/classify_new_traits.py` 존재·실행 가능.
7. 테스트 통과(slim/detail 분리, detail 렌더 fetch, 초기화, sync_player_detail, 파서·attach 유지).

## 핵심 결정
- **#1 A안(지연 로드)** — 초기 무게 −76%(gzip 0.90→0.22MB). 대가: file:// 직접 열기 시 드릴다운 미동작(로컬은 http 서버로 확인). 자기완결 테스트는 fetch가 script/link 태그가 아니라 통과.
- 부동소수 4자리 반올림 동반(표시 무영향, 무료).
- **#7**은 4건 중 가치 최저(동작 코드 변경)나 사용자 요청 — 재수집 없이 코드 통합만.
- **#8**은 단순 dep 추가가 아니라 재현 스크립트+optional 그룹으로(의미 있는 형태).

## 채택된 가정
- details.json은 same-origin 상대경로 fetch(배포 루트=out). Vercel outputDirectory=out이라 `/details.json` 서빙됨.
- 상세 지연 로드는 첫 클릭 시 1회 fetch·캐시(idle 프리페치는 선택). 대부분 클릭 전 유휴에 받아둘 수 있음.
- playerdetail 증분 기준은 player_trait(spid) 미보유. bio는 그 과정에서 부수 획득.

## 남은 리스크
- details.json 미배포/경로 오타 시 드릴다운 전멸 → 안내 문구 + 배포 후 실제 클릭 검증 필수.
- #7 리팩터가 수집 회귀 유발 가능 → mock 테스트 + `--limit`로 소량 실검증.
- file:// 워크플로 상실(문서화로 완화).

## 작업
1. `pyproject.toml` tools 그룹 + `scripts/classify_new_traits.py` 커밋(#8). 검증: 스크립트가 NEW_TRAIT_CODES 출력.
2. `render.py` 필터 초기화 아이콘 버튼 + 핸들러 + CSS(#5). 검증: 테스트+프리뷰(초기화 동작).
3. `playerinfo.py` `sync_player_detail` 신설·`sync_player_bio/trait` 제거, `cli.py` `playerdetail`(#7). 검증: mock 테스트, `--limit 3` no-op/소량.
4. `export.py` slim+details 분리·반올림(#1). 검증: index.html에 zones 없음·details.json 생성.
5. `render.py` 드릴다운 fetch(details.json) 지연 로드·로딩/에러 처리(#1). 검증: 로컬 http 서버로 클릭.
6. 테스트: 분리·fetch·초기화·sync_player_detail·파서 유지.
7. export 재생성 → 로컬 http 서버 헤드리스 검증(드릴다운·초기화) → 커밋(feat/refactor + chore out/).

## 실행 규칙
① 검증 실패 시 다음 작업으로 넘어가지 말고 보고 ② 작업 단위 커밋 ③ 계획 밖 변경 금지(필요 발견 시 보고 후 계획 갱신).
