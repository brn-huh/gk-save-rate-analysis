# 계획: 선수 국가·클럽 수집 + 카드 표시 (fc-info)

작성: 2026-07-14 · plan-forge (심도: 중대, 인프라 기존 확장) · 대상: `playerinfo.py`, `db.py`, `export.py`, `render.py`, `cli.py`, tests

## 목표
GK 선수(pid)별 **국가(코드+명)·클럽 경력**을 fc-info 상세페이지에서 1회 수집해 DB에
저장하고, 카드 드릴다운에 국기·국가명·클럽이력을 표시한다. (1강 OVR은 이미 `player_info.ovr`로 수집·표시 중)

## 조사로 확정된 사실 (기술 검증 완료)
- fc-info 수집 인프라 기존 존재: `playerinfo.py`의 httpx 클라이언트(`_new_client`), `player_info` 테이블.
- 국가·클럽은 검색 API(`/local-api/v2/players/search`)엔 **없음**. 상세 JSON 엔드포인트도 없음(404).
- 상세 페이지 `GET /player/{spid}?grade=1` → **200**, HTML에 국가·클럽 포함. 6명(멀티클럽 포함) 파싱 검증:
  - 국가코드: `countries/smallflags/(\d+)\.png`
  - 국가명: `alt="nationality"/><span>([^<]+)</span>`
  - 클럽: `PlayerClubHistory_year__\w+">[^<]*</div><div>([^<]+)</div>` (반복 = 여러 클럽)
- 국가·클럽은 **선수(pid) 단위** → pid당 1회. 우리 GK 고유 pid = **881개** (약 15분 @1req/s, 증분).

## In-scope
- 새 테이블 2개(pid 키): `player_bio`(국가), `player_club`(클럽 이력).
- `playerinfo.py`에 `sync_player_bio()` — need pid만 상세페이지 1회씩, 파싱 후 upsert. 저속·증분·재시작 안전.
- 새 CLI 서브커맨드 `gksave playerbio` (881요청 장시간 잡이라 기존 playerinfo와 분리).
- `attach_bio()` + export payload에 `bio` 포함 + 카드 드릴다운에 국기·국가명·클럽 표시.
- **리더보드 검색에 국가/클럽 검색 추가** (2026-07-14 범위 확장): 이름 검색(기존 쉼표 OR)과
  **별개인** "국가/클럽" 검색 입력 하나. 입력값이 카드의 국가명 **또는** 클럽명에 부분일치하면 표시.
  (사용자 확정: 국가·클럽은 한 번에 하나만 검색 — 둘을 AND로 조합하는 경우 없음.)
- 파서·attach·스키마·검색 테스트.

## Out-of-scope (로드맵)
- 국가/클럽 검색의 자동완성·드롭다운(이번엔 단순 텍스트 부분일치).
- 국가명 다국어, 리더보드 목록(행)에 국기 표시(드릴다운에만).
- 넥슨 공식 경로로의 이전(현재 fc-info가 유일 소스).

## 수용 기준
1. `gksave playerbio` 실행 시 `player_bio`에 없는 pid만 상세요청, 완료 후 우리 GK 881 pid 대부분에 국가코드가 채워진다.
2. 멀티클럽 선수(예: 부폰)는 `player_club`에 순서대로 여러 행(중복 클럽은 제거).
3. 재실행 시 이미 있는 pid는 재요청하지 않는다(증분). 중간 중단해도 받은 pid는 보존된다.
4. export 후 카드 드릴다운에 국기(넥슨 CDN)·국가명·클럽이력이 보인다(bio 있는 카드).
5. bio 없는 카드는 드릴다운이 회귀 없이 그대로(국가/클럽 섹션만 생략).
6. 국가명 파싱 실패해도 국가코드만 있으면 국기는 표시된다(코드가 견고한 키).
7. 리더보드에 "국가/클럽" 검색 입력이 이름 검색과 별개로 있고, "이탈리아" 입력 시 이탈리아 GK
   카드만, "유벤투스" 입력 시 유벤투스 출신 카드만 남는다(국가명 OR 클럽명 부분일치).
8. 이름 검색과 국가/클럽 검색은 각자 적용(둘 다 입력 시 AND). 국가·클럽끼리 AND 조합은 없음.
9. 테스트 통과(파서 3종·attach·스키마·검색 매처).

## 핵심 결정과 이유
- **스키마: pid 키 신규 테이블 2개** (비가역 — 승인 포인트).
  - `player_bio (pid BIGINT PK, nation_code INTEGER, nation_name VARCHAR, fetched_at TIMESTAMP DEFAULT now())`
  - `player_club (pid BIGINT, ord INTEGER, club_name VARCHAR, PRIMARY KEY(pid, ord))`
  - 이유: 국가·클럽은 시즌 무관 선수 속성 → spid(카드)별 저장은 대량 중복. `player_info`(spid키)와 분리하고 pid로 join(기존 `attach_info`의 by_pid 역채움과 동일 패턴). `CREATE TABLE IF NOT EXISTS`라 마이그레이션 위험 낮음(순수 추가).
- **수집: 상세페이지 HTML 정규식 파싱** (헤드리스 브라우저 불필요). 기존 httpx 클라이언트가 200을 받으므로 가장 가벼운 경로. 저속(≈1req/s)·증분·1회.
- **국가코드가 1차 키, 국가명은 부가**: 국기 URL은 코드로 만들고 코드가 견고. 국가명은 파싱 실패 시 NULL 허용.
- **표시 위치**: 카드 드릴다운(상세)만. 목록 행엔 안 넣음(정보량·모바일 폭 고려).

## 채택된 가정 장부
- pid(spid 뒤 6자리)는 실선수 고유 — 서로 다른 선수가 pid를 공유하지 않음(기존 by_pid 역채움이 이미 이 가정 위에 있음).
- 클럽 순서 = 페이지 표기 순서. 중복 클럽명은 첫 등장만 유지.
- 국가명은 상세페이지 텍스트에서 파싱(코드→명 별도 사전 안 씀 — YAGNI).
- 수집은 수동 트리거 1회(무인 수집 금지 원칙 유지).

## 남은 리스크
- **파싱 취약성**: fc-info가 Next.js CSS 모듈명(`PlayerClubHistory_*`)이나 `alt="nationality"`를 바꾸면 파서가 깨짐. → 테스트로 고정, 1회 수집이라 깨지면 재실행 시 즉시 인지. 국가코드 정규식은 URL 기반이라 상대적으로 안정.
- **약관**: robots `*`=Allow + `use=reference`, 저속·소량·1회·비재배포 조건 준수. ClaudeBot 전면차단은 Anthropic 크롤러 대상이며 우리 httpx 클라이언트와 별개.
- **요청량 881**: 저속 지연으로 예의. 실패 pid는 다음 실행에서 자동 재시도(증분).

## 작업
1. `db.py` — `player_bio`·`player_club` 테이블 추가(스키마 문자열에). 검증: 새 DB 연결 시 테이블 생성 확인.
2. `playerinfo.py` — 파서 상수 3종(`_NAT_CODE`,`_NAT_NAME`,`_CLUB` 정규식) + `parse_bio(html)->(code,name,[clubs])` + `sync_player_bio(con,...)`(need pid 루프·상세GET·저속·upsert). 검증: `parse_bio`에 야신/부폰 HTML 스니펫 넣어 기대값.
3. `cli.py` — `playerbio` 서브커맨드(+`--limit N` 소량 시험용, `--delay`). 검증: `--limit 3` 실행 시 3 pid만 요청·저장.
4. `playerinfo.py` — `attach_bio(con, leaderboard)`: 카드에 `c['bio']={nation_code,nation_name,clubs}` (pid join). 검증: 유닛 테스트.
5. `export.py` — payload/카드에 bio 포함(build_payload에서 attach_bio 호출). 검증: export 후 JSON에 bio 존재.
6. `render.py` — 드릴다운(`detailHtml`)에 국기(`countries/smallflags/{code}.png`)·국가명·클럽 칩/리스트. bio 없으면 생략. 검증: 테스트 + 프리뷰.
7. `render.py` — 리더보드 컨트롤에 "국가/클럽" 검색 입력 추가 + `matchNatClub(c,q)`(국가명 OR 클럽명 부분일치) 필터 결합. 이름 검색과 별개(AND). 검증: node 매처 테스트 + 프리뷰.
8. 테스트: 파서 3종(스니펫), attach_bio(pid 매칭), 스키마 존재, render 드릴다운(bio 있을 때 국기·클럽), matchNatClub 매처.
9. 수집 실행: `playerbio --limit 5`로 종단 검증 → 전체 실행(≈15분, DB 락 없을 때) → export 재생성 → 프리뷰 확인.
10. 커밋: ① feat 코드 ② chore out/ 재생성.

## 작업 순서·체크포인트
- 1→2→3 (수집 경로 먼저 완성, `--limit`로 소량 검증하면 안전 체크포인트) → 4→5→6 (표시) → 7 테스트 → 8 실제 수집·표시 확인 → 9 커밋.
- 병렬 가능: 6(render)·7(테스트 일부)는 2~5와 독립적으로 초안 가능하나 순차 권장.
- 중단 안전: 8의 전체 수집은 pid별 커밋이라 언제 끊어도 받은 만큼 남음.

## 실행 규칙
① 검증 실패 시 다음 작업으로 넘어가지 말고 보고 ② 작업 단위 커밋 ③ 계획 밖 변경 금지(필요 발견 시 보고 후 계획 갱신).

## 회고 (2026-07-14 실행 후)
- **0단계 조사가 계획을 통째로 바꿨다.** 처음엔 "헤드리스 브라우저로 스크래핑"으로 갈 뻔했는데,
  조사에서 ① `playerinfo.py`+`player_info` 인프라가 이미 있고 ② 1강 OVR은 이미 수집 중이며
  ③ 기존 httpx 클라이언트가 상세페이지를 200으로 받는다는 걸 확인해, 헤드리스 없이 정규식 파싱으로
  훨씬 가볍게 끝났다. **`gksave <sub> --help`로 기존 커맨드를 먼저 훑는 게 이 저장소의 필수 0단계.**
- **범위가 실행 중 커졌다**(클럽 검색이 로드맵→인스코프, 국가 검색 추가). 승인 후 추가 요구가 와도
  계획 문서를 즉시 갱신(In/Out-scope·수용기준·작업)하고 진행한 게 드리프트를 막았다.
- **fc-info = 넥슨 Open API 프론트엔드.** 국기·시즌·선수 이미지는 전부 넥슨 CDN 직접(핫링크 가능).
  국가코드→`countries/smallflags/{code}.png`. 국가·클럽만 fc-info 상세 HTML에 있고 검색 API엔 없다.
- **pid 단위 수집이 핵심 절감.** 국가·클럽은 시즌 무관 선수 속성 → spid(2592) 아닌 pid(881)당 1회.
  기존 `attach_info`의 by_pid 역채움 패턴을 그대로 재사용.
- **DuckDB 단일쓰기 함정 재확인.** 수집 중엔 read_only 조회도 락에 막힌다([[project-state]]). 장시간
  잡은 백그라운드로 돌리고 완료 알림 후 export. 진행률은 로그 1줄뿐이라 중간 확인 불가.
- **함정 후보(다음 세션)**: 국가 없는 38명은 `player_bio`에 NULL 행이 남아 증분 재실행이 재요청하지
  않는다. fc-info가 나중에 채우면 해당 pid를 지우고 `playerbio` 재실행해야 갱신됨. 파서는 fc-info
  Next.js 클래스명(`PlayerClubHistory_*`)·`alt="nationality"`에 의존 → 사이트 개편 시 취약(테스트로 고정).
