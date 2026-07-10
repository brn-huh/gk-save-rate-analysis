# payload 무손실 압축 + 롤링 30일 통계 창

작성: 2026-07-09 · 등급: 중대(데이터 포맷 비가역 변경)

## 배경

수집 성능 조사에서 두 개의 독립적인 손실을 실측했다.

- **가동률 14.5%** — 수집기가 152시간 중 22시간만 돌았다. `collect.sh`가 대화형 프롬프트에서 블로킹한다.
- **가동 중에도 5.64매치/초** — 레이트 예산은 15/초. 유저당 17.9초 중 API가 강제하는 건 6.9초뿐이고,
  `have_match` 0.43초, 진행률 카운트 0.008초를 빼면 **약 10.6초가 INSERT**다. 매치마다 21KB JSON을
  autocommit 3문장(raw 1 + frontier 2)으로 11.4GB 테이블에 쓰는 동안 in-flight 요청은 0이다.

이 계획은 그 전제조건인 **쓰기량 감소**만 다룬다. 인서트 배치·오프로딩·파이프라이닝·무인 루프는 후속.

부수적으로, 실제 데이터가 6/1~7/8의 38일치뿐이고 `COLLECT_MIN_DATE = "2026-03-26"`이 한 번도
작동한 적 없는 죽은 설정임이 드러났다. 지평선은 날짜가 아니라 `user_pages=3`(유저당 최근 300경기)이 정한다.

## 목표

1. 매치당 쓰기량 21.0KB → 2.8KB (zlib-6, 실측 13.2%)
2. 통계를 롤링 30일 창으로 (`shot`/`gk_match` 기반이라 되돌릴 수 있는 결정)
3. 수집 컷을 롤링 35일로 (창보다 5일 여유, `reached_old` 조기중단으로 요청 절약)

## In-scope

- `raw_match.payload` 를 `JSON` → `BLOB`(zlib-6)
- 인코딩/디코딩 단일 지점 `src/gksave/codec.py`
- 기존 11.4GB DB 마이그레이션 (새 파일 생성 후 스왑)
- `COLLECT_MIN_DATE` → `COLLECT_WINDOW_DAYS = 35`
- `update.sh` export 에 `--days 30`, `collect.sh` 에 `--days 35`

## Out-of-scope (로드맵)

인서트 배치 · 쓰기 오프로딩 · 유저 파이프라이닝 · 락 인지형 무인 루프 · frontier 우선순위 ·
`raw_match` 프루닝(`compact --keep-days`) · payload 프루닝(원본 무손실 유지).

## 핵심 결정

| 결정 | 근거 |
|---|---|
| 무손실 압축, payload 폐기 없음 | 파싱 로직(GSAx 캘리브레이션)이 아직 바뀐다. 재파싱 능력 필수 |
| zlib-6, 표준 라이브러리 | 2.76KB/13.2%. 의존성은 httpx·duckdb 둘뿐인 기조 유지. zstd-3과 크기 차 5%, 압축·해제가 병목인 지점 없음 |
| in-place `ALTER` 금지, 새 파일 + 스왑 | DuckDB 1.5.4는 `DROP COLUMN`·`DELETE` 후 `CHECKPOINT` 해도 회수율 0% (실측). 부수효과로 원본 무손상 = 롤백 가능 |
| 대량 삽입은 base64 + COPY | `executemany` 대비 8.2배 (1.5분 vs 12.2분, 실측). `agg.py`의 기존 CSV+COPY 패턴과 동일 |
| `decode_payload`는 `bytes`와 `str` 모두 수용 | 3줄로 `.bak` 백업·롤백 경로가 계속 읽힌다 |
| 컬럼명 `payload` 유지 | `agg.py`의 `SELECT match_id, payload` 가 그대로 산다 |
| 통계 창은 롤링 30일 (월 1일 앵커 아님) | 월 앵커는 매달 1일 창이 61→31일로 반토막. 실측상 창 절반이면 통과 셀의 24.8%가 사라진다 |
| 창은 export 시점 파라미터 | 집계는 `shot`/`gk_match`를 읽는다. `raw_match` 보존과 무관하며 언제든 다시 뽑을 수 있다 |

## 마이그레이션 전 기준값

계획 작성(7/9) 이후에도 수집이 계속 돌아 아래 값은 전부 낡았다. **2026-07-10 실행 시점에
재측정한 값으로 대체한다.** 또한 `parsed_at IS NULL` 이 60,000건이라 AC7·AC8 이 성립하지
않아, 마이그레이션 직전에 `gksave build`(증분)를 한 번 돌려 NULL 을 0으로 만들었다.

```
[낡음 — 7/9 작성 시점]
raw_match  446,802      frontier 205,455 (done 4,424 / pending 201,031)
gk_match   820,921      shot     4,214,106
DB 파일    11.4 GB      match_date 범위 2026-06-01 ~ 2026-07-08

[실측 — 7/10, gksave build 직후]
raw_match  656,802      frontier 253,876 (done 6,662 / pending 247,214)
gk_match 1,204,518      shot     6,191,992
parsed_at  NULL 0 / 비NULL 656,802
DB 파일    15.04 GB     match_date 범위 2026-06-01 ~ 2026-07-10
payload 압축 추정 1.75 GB (압축률 13.3% — 표본 200건 실측, 계획서 13.2% 와 일치)
```

롤링 30일 창의 실제 비용도 재측정했다: 게이트 통과 카드 2,051 → **1,979장(-72, -3.5%)**.
계획서가 우려한 "창 절반이면 24.8% 소실" 보다 훨씬 가볍다.

## 수용 기준

| # | 기준 |
|---|---|
| AC1 | 새 `data/gksave.duckdb` ≤ 3.5GB (payload 압축 추정 1.75GB + 나머지 테이블) |
| AC2 | raw_match 656,802행 |
| AC3 | 무작위 1,000행에서 `decode_payload(신규)` == 구 payload의 `json.loads` |
| AC4 | `parsed_at` NULL 0 / 비NULL 656,802 (이전과 동일) |
| AC5 | frontier 253,876행, state 분포 동일 (done 6,662 / pending 247,214) |
| AC6 | gk_match 1,204,518행, shot 6,191,992행 |
| AC7 | `gksave build`(증분) 파싱 대상 0건 |
| AC8 | `gksave build --full` 후 gk_match·shot 행수가 기준값과 동일 |
| AC9 | `gksave collect --max 50` 후 새 행이 BLOB으로 저장·디코드됨 |
| AC10 | `pytest` 전체 통과 |
| AC11 | 원본이 `data/gksave.duckdb.bak` 으로 보존 (AC1~AC10 통과 전 삭제 금지) |
| AC12 | `update.sh` 산출 `out/index.html` 의 `since` 가 30일 전 날짜 |

## 작업

### T1. `src/gksave/codec.py` + 테스트 (TDD)

`tests/test_codec.py` 를 먼저 쓴다.

```python
def encode_payload(detail: dict) -> bytes         # json.dumps(ensure_ascii=False) → utf-8 → zlib.compress(_, 6)
def decode_payload(v: bytes | bytearray | str | dict) -> dict   # bytes→해제, str→json.loads, dict→그대로
```

검증: `pytest tests/test_codec.py -q` · 왕복 일치 + 한글 보존 + `str`/`dict` 입력 수용
커밋: `feat: payload 압축 코덱 추가`

### T2. `src/gksave/db.py` 스키마

`db.py:24` `payload JSON NOT NULL` → `payload BLOB NOT NULL`

검증: `python -c "from gksave.db import connect_memory; print(connect_memory().execute(\"SELECT data_type FROM duckdb_columns() WHERE table_name='raw_match' AND column_name='payload'\").fetchone())"` → `BLOB`

### T3. `src/gksave/collect.py` 쓰기 경로

`_store_match`(collect.py:91) 와 `snowball_async`(collect.py:259) 의 INSERT 두 곳에서
`json.dumps(detail, ensure_ascii=False)` → `encode_payload(detail)`

### T4. `src/gksave/agg.py` 읽기 경로

`agg.py:81` `detail = json.loads(payload) if isinstance(payload, str) else payload` → `decode_payload(payload)`

### T5. 테스트 갱신

`tests/test_agg.py:13` 의 `INSERT INTO raw_match (match_id, payload)` 가 `encode_payload(...)` 를 넣도록.

검증: `pytest -q` 전체 통과 (AC10). 여기까지 실제 DB는 건드리지 않는다.
커밋: `feat: raw_match.payload 를 zlib BLOB 으로 전환`

### T6. `scripts/migrate_compress.py` 신설 — 신규 파일 생성만, 스왑 없음

1. `data/gksave.duckdb` 를 `read_only=True` 로 연다 (`db.connect(read_only=True)` 는 SCHEMA를 적용하지 않음 — db.py:144 확인함)
2. `data/gksave.new.duckdb` 를 `db.SCHEMA` 로 생성
3. `ATTACH` 로 `frontier` / `meta_spid` / `meta_season` / `gk_match` / `shot` 를 `INSERT INTO ... SELECT *` 복사
4. `raw_match` 는 `fetchmany(1000)` 스트리밍 → `encode_payload` → base64 임시 CSV → `from_base64()` 로 `COPY`
   (`match_date` · `fetched_at` · `parsed_at` 전부 보존)
5. `CHECKPOINT` 후 AC1~AC6 자체 검증 출력. 하나라도 실패하면 비영으로 종료하고 신규 파일을 남긴다

검증: 스크립트 출력이 AC1~AC6 전부 PASS. 예상 소요 약 5분(압축 2분 + COPY 1.5분 + ATTACH 복사 30초)
중단해도 안전한 체크포인트 — 원본은 무손상이므로 신규 파일만 지우고 재시도

### T7. 스왑 + 실DB 검증

```
mv data/gksave.duckdb data/gksave.duckdb.bak
mv data/gksave.new.duckdb data/gksave.duckdb
gksave build                    # AC7: 파싱 0건
gksave build --full             # AC8: gk_match 1,204,518 / shot 6,191,992
gksave collect --max 50 --days 35   # AC9
```

커밋: `chore: DB 마이그레이션 (payload 압축)` — `data/` 는 .gitignore 대상이라 코드만

### T8. `src/gksave/config.py` 롤링 수집 창

`config.py:52` `COLLECT_MIN_DATE = "2026-03-26"` → `COLLECT_WINDOW_DAYS = 35`
`collect.py:288`(run_async) 과 `collect.py:339`(run) 의 `since is None` 기본값을
`datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=COLLECT_WINDOW_DAYS)` 로.

검증: `gksave collect --max 1` 로그의 "수집 하한 날짜" 가 오늘-35일

### T9. 스크립트 창 설정

- `scripts/update.sh`: `gksave export --gate 50 --days 30 --out out`
- `scripts/collect.sh`: `gksave collect --concurrency 12 --days 35 --max "$MAX" ...`

검증: `./scripts/update.sh` 후 `out/index.html` 의 `since` 가 30일 전 (AC12)
커밋: `feat: 통계 롤링 30일 창 + 수집 롤링 35일 컷`

### T10. 리더보드 재생성 + 배포

`./scripts/update.sh` → `git add out && git commit` → push (Vercel 자동 재배포)

## 작업 순서 근거

T1~T5는 실제 DB를 건드리지 않고 인메모리 테스트로만 검증된다. T4의 관대한 디코드 덕에
**T5 시점의 코드가 구형 DB(JSON 컬럼)도 계속 읽을 수 있어**, 마이그레이션 전에 안전하게 커밋할 수 있다.
T6은 원본을 read-only로만 열고 신규 파일을 만든다. 되돌리려면 신규 파일만 지우면 된다.
T7의 `mv` 두 줄이 유일한 비가역 지점이고, 그 직전까지 AC1~AC6이 전부 통과한 상태다.
T8~T9는 데이터와 무관해 언제 해도 되지만, 마이그레이션 검증 로그를 오염시키지 않도록 뒤에 둔다.

병렬 가능 지점 없음(전부 직렬 의존). 중단해도 안전한 체크포인트: T5 커밋 후, T6 완료 후.

## 실행 중 발견 (2026-07-10)

계획서가 놓쳤거나 틀렸던 것들. 다음에 비슷한 마이그레이션을 할 때 그대로 쓸 수 있게 남긴다.

| 발견 | 영향 |
|---|---|
| `card_stats`·`shot_readable` 은 **뷰**다 | `SHOW TABLES` 에 섞여 나온다. 복사했으면 스키마가 만든 뷰와 충돌했다 |
| `raw_match` 컬럼 순서가 원본과 새 스키마에서 **다르다** | 원본 `(match_id, payload, fetched_at, match_date, parsed_at)` vs 새 `(match_id, match_date, payload, ...)`. `SELECT *` 였으면 값이 조용히 어긋났다 |
| T5 가 `test_agg.py` 만 짚었다 | `test_dates.py`·`test_views.py` 도 raw JSON 을 INSERT 해 `zlib.error` 로 깨졌다 |
| T7·T9 가 `--max` 라 썼다 | 실제 플래그는 `--max-matches` |
| `--days` 는 이미 CLI 전 서브커맨드에 있었다 | T9 는 스크립트 두 줄 수정뿐 |
| 압축이 계획 추정(2분)보다 느렸다 | 656,802건에 **930초**. 병목은 zlib 이 아니라 15GB 원본에서 JSON 을 읽고 파싱하는 비용 |

실측 결과: **15.04GB → 2.08GB (13.8%)**. 압축률은 표본 예측(13.3%)과 일치.

## 남은 리스크

- **`parsed_at` 유실이 조용한 오염을 부른다.** 증분 빌드가 `parsed_at IS NULL` 로 대상을 고르고
  결과를 `gk_match`·`shot` 에 **append** 하므로, NULL 로 리셋되면 재파싱분이 중복 적재된다. AC4가 잡는다.
- 신규 코드로 구형 DB에 **쓰면** `bytes` → JSON 컬럼 삽입이 실패한다. 조용한 손상이 아니라 즉시 예외라 수용.
- 마이그레이션 중 크래시 시 신규 파일이 반쯤 찬다. 원본 무손상이므로 지우고 재시도.
- 디스크: 원본 15.04GB + 신규 약 3GB + 임시 base64 CSV 약 2.4GB = 약 21GB. 여유 188GB.
- **`data/` 는 git 에 없다.** 이 DB 를 잃으면 재수집에 몇 주가 걸린다. AC11(원본 `.bak` 보존)이
  이 작업에서 가장 중요한 규칙이다.

## 실행 규칙

1. 검증 실패 시 다음 작업으로 넘어가지 말고 보고한다.
2. 작업 단위로 커밋한다.
3. 계획 밖 변경 금지. 필요가 발견되면 보고 후 계획을 갱신한다.
